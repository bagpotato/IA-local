from __future__ import annotations

import io
import re
import sys
import tempfile
import threading
from pathlib import Path

from agent.config import Settings
from agent.utils.proc import run_limited


class TTSError(Exception):
    pass


_lock = threading.Lock()
_state: dict = {"model": None, "device": None}


def _pick_device(settings: Settings) -> str:
    if settings.whisper_device in ("cpu", "cuda"):
        return settings.whisper_device
    try:
        import ctranslate2

        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


def _get_model(settings: Settings, force_cpu: bool = False):
    with _lock:
        if _state["model"] is not None and not (force_cpu and _state["device"] != "cpu"):
            return _state["model"]
        from faster_whisper import WhisperModel

        device = "cpu" if force_cpu else _pick_device(settings)
        compute = "float16" if device == "cuda" else "int8"
        try:
            model = WhisperModel(settings.whisper_model, device=device, compute_type=compute)
        except Exception:
            if device != "cuda":
                raise
            device, compute = "cpu", "int8"
            model = WhisperModel(settings.whisper_model, device=device, compute_type=compute)
        _state.update(model=model, device=device)
        return model


def transcribe(settings: Settings, audio: bytes, language: str | None = None) -> str:
    def run(model) -> str:
        segments, _info = model.transcribe(
            io.BytesIO(audio), language=language or None, vad_filter=True, beam_size=5
        )
        return " ".join(s.text.strip() for s in segments).strip()

    model = _get_model(settings)
    try:
        return run(model)
    except Exception:
        if _state["device"] == "cuda":  # fallos de CUDA aparecen al transcribir: reintento en CPU
            return run(_get_model(settings, force_cpu=True))
        raise


def clean_for_speech(text: str) -> str:
    text = re.split(r"\n\s*Fuentes:", text, maxsplit=1)[0]
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"[*_#`>|~]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:3000]


def _piper(settings: Settings, text: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.wav"
        cmd = [
            sys.executable, "-m", "piper",
            "-m", settings.piper_voice,
            "--data-dir", str(settings.piper_data_dir),
            "-f", str(out),
        ]
        res = run_limited(cmd, input_bytes=(text + "\n").encode("utf-8"), timeout=90, max_out=20000)
        if res.returncode != 0 or not out.exists():
            raise TTSError(f"Piper falló: {res.output[-300:].strip()}")
        data = out.read_bytes()
    if not data.startswith(b"RIFF"):
        raise TTSError("Piper no devolvió un WAV válido")
    return data


def _gtts(settings: Settings, text: str) -> bytes:
    try:
        from gtts import gTTS

        buf = io.BytesIO()
        gTTS(text, lang=settings.tts_lang).write_to_fp(buf)
        return buf.getvalue()
    except Exception as exc:
        raise TTSError(f"gTTS falló: {exc.__class__.__name__}") from exc


def synthesize(settings: Settings, text: str) -> tuple[bytes, str]:
    """Devuelve (audio, mime). WAV con Piper; MP3 con gTTS solo si TTS_ALLOW_ONLINE=true."""
    text = clean_for_speech(text)
    if not text:
        raise TTSError("No hay texto para sintetizar")
    try:
        return _piper(settings, text), "audio/wav"
    except TTSError:
        if settings.tts_allow_online:
            return _gtts(settings, text), "audio/mpeg"
        raise
