from __future__ import annotations

import base64
import io
import os
import shutil
import subprocess

from pydantic import BaseModel, Field

from agent.config import Settings
from agent.registry import Ctx, ToolSpec
from agent.security import fs
from agent import llm

MAX_SIDE = 1280
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
DEFAULT_PROMPT = "Describe con detalle lo que ves y transcribe cualquier texto visible."


class DescribeImageArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=500, description="Imagen dentro del workspace")
    prompt: str = Field(DEFAULT_PROMPT, max_length=1000, description="Qué quieres saber de la imagen")


class LookArgs(BaseModel):
    prompt: str = Field(DEFAULT_PROMPT, max_length=1000, description="Qué quieres saber de lo que se ve")


def to_jpeg_bytes(img) -> bytes:
    """Reescala a 1280 px como máximo y comprime a JPEG."""
    from PIL import Image

    img = img.convert("RGB")
    w, h = img.size
    biggest = max(w, h)
    if biggest > MAX_SIDE:
        scale = MAX_SIDE / biggest
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def b64_from_image(img) -> str:
    return base64.b64encode(to_jpeg_bytes(img)).decode("ascii")


def b64_from_bytes(raw: bytes) -> str:
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:
        raise ValueError("Imagen no válida") from exc
    return b64_from_image(img)


def describe_b64(settings: Settings, image_b64: str, prompt: str = DEFAULT_PROMPT) -> str:
    return llm.vision_describe(settings, image_b64, prompt or DEFAULT_PROMPT)


def capture_screen():
    from PIL import Image

    wayland = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
    if wayland:
        grim = shutil.which("grim")
        if grim:
            res = subprocess.run([grim, "-t", "png", "-"], capture_output=True, timeout=15, check=False)
            if res.returncode == 0 and res.stdout:
                return Image.open(io.BytesIO(res.stdout))
            raise RuntimeError("grim no pudo capturar la pantalla")
    import mss

    with mss.mss() as sct:
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        shot = sct.grab(monitor)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def capture_webcam(index: int = 0):
    import cv2
    from PIL import Image

    cap = cv2.VideoCapture(index)
    frame = None
    try:
        if not cap.isOpened():
            raise RuntimeError("No se pudo abrir la webcam")
        for _ in range(5):  # descarta los primeros fotogramas mientras se ajusta la exposición
            ok, f = cap.read()
            if ok:
                frame = f
    finally:
        cap.release()
    if frame is None:
        raise RuntimeError("La webcam no devolvió imagen")
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def _wrap(text: str) -> str:
    return "<vision_output untrusted>\n" + text.replace("</vision_output", "</vision_output_") + "\n</vision_output>"


def _describe_image(ctx: Ctx, a: DescribeImageArgs) -> str:
    try:
        target = fs.resolve_in_workspace(a.path, ctx.settings.workspace_dir)
        if target.suffix.lower() not in IMAGE_EXT or not target.is_file():
            return "ERROR: no es una imagen válida del workspace"
        if target.stat().st_size > ctx.settings.max_image_bytes:
            return "ERROR: imagen demasiado grande"
        b64 = b64_from_bytes(target.read_bytes())
    except (fs.PathNotAllowed, ValueError, OSError) as exc:
        return f"ERROR: {exc}"
    return _wrap(describe_b64(ctx.settings, b64, a.prompt))


def _look_at_screen(ctx: Ctx, a: LookArgs) -> str:
    try:
        b64 = b64_from_image(capture_screen())
    except Exception as exc:
        return f"ERROR: no se pudo capturar la pantalla ({exc})"
    return _wrap(describe_b64(ctx.settings, b64, a.prompt))


def _look_through_webcam(ctx: Ctx, a: LookArgs) -> str:
    try:
        b64 = b64_from_image(capture_webcam(ctx.settings.webcam_index))
    except Exception as exc:
        return f"ERROR: no se pudo usar la webcam ({exc})"
    return _wrap(describe_b64(ctx.settings, b64, a.prompt))


SPECS = [
    ToolSpec(
        "describe_image",
        "Describe una imagen del workspace con el modelo de visión.",
        DescribeImageArgs, _describe_image, "files", "vision",
    ),
    ToolSpec(
        "look_at_screen",
        "Captura la pantalla del equipo y la describe según el prompt.",
        LookArgs, _look_at_screen, "screen", "vision", taints=True,
    ),
    ToolSpec(
        "look_through_webcam",
        "Hace una foto con la webcam y la describe según el prompt.",
        LookArgs, _look_through_webcam, "screen", "vision",
    ),
]
