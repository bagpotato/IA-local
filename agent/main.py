from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from agent import llm
from agent.config import Settings, get_settings
from agent.models import (
    MODEL_ID,
    SESSION_RE,
    AudioChatResponse,
    ChatCompletionRequest,
    ChatRequest,
    ChatResponse,
    SpeechRequest,
)
from agent.prompts import build_system_prompt
from agent.registry import Ctx, enabled_tools
from agent.storage.db import Store
from agent.tools import audio, vision, web
from agent.utils.logger import get_logger, log_event, setup_logging

log = get_logger("main")
Runner = Callable[..., AsyncIterator[tuple[str, str]]]
LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z]{2,4})?$")
IMAGE_PROMPT = "Describe la imagen con detalle y transcribe cualquier texto visible."


class State:
    def __init__(self, settings: Settings, store: Store, runner: Runner) -> None:
        self.settings = settings
        self.store = store
        self.runner = runner
        self.sem = asyncio.Semaphore(settings.max_concurrent)


def default_runner(settings: Settings) -> Runner:
    if settings.agent_engine == "crewai":
        from agent.engine_crewai import run_crewai

        return run_crewai
    from agent.engine_native import run_native

    return run_native


# --- autenticación ---

def _extract_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip()


def make_auth(settings: Settings):
    expected = settings.agent_api_key.encode("utf-8")

    async def require_key(request: Request) -> None:
        given = _extract_key(request).encode("utf-8")
        if not hmac.compare_digest(given, expected):
            raise HTTPException(status_code=401, detail="API key inválida", headers={"WWW-Authenticate": "Bearer"})

    return require_key


# --- utilidades ---

def decode_image(data: str, max_bytes: int) -> bytes:
    if data.startswith("data:"):
        _, _, data = data.partition(",")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="image_b64 no es base64 válido")
    if not raw:
        raise HTTPException(status_code=400, detail="Imagen vacía")
    if len(raw) > max_bytes:
        raise HTTPException(status_code=413, detail="Imagen demasiado grande")
    return raw


async def image_note(settings: Settings, raw: bytes) -> str:
    try:
        b64 = await asyncio.to_thread(vision.b64_from_bytes, raw)
        desc = await asyncio.to_thread(vision.describe_b64, settings, b64, IMAGE_PROMPT)
    except ValueError:
        return "[La imagen adjunta no es válida]"
    except llm.OllamaError as exc:
        return f"[No se pudo analizar la imagen: {exc}]"
    desc = desc.replace("</vision_output", "</vision_output_")
    return f"[Imagen adjunta. Descripción del modelo de visión:]\n<vision_output untrusted>\n{desc}\n</vision_output>"


async def read_upload(file: UploadFile, max_bytes: int) -> bytes:
    data = await file.read(max_bytes + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Fichero vacío")
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail="Audio demasiado grande")
    return data


def build_messages(
    settings: Settings, history: list[dict], user_text: str, extra_system: str = ""
) -> list[dict]:
    names = {s.name for s in enabled_tools(settings)}
    return (
        [{"role": "system", "content": build_system_prompt(names, extra_system)}]
        + history
        + [{"role": "user", "content": user_text}]
    )


async def stream_events(state: State, ctx: Ctx, messages: list[dict]) -> AsyncIterator[tuple[str, str]]:
    async with state.sem:
        async for event in state.runner(state.settings, ctx, messages):
            yield event


async def run_session(state: State, session_id: str, user_text: str) -> tuple[str, Ctx]:
    settings = state.settings
    history = state.store.history(session_id, settings.history_turns)
    ctx = Ctx(settings=settings, session_id=session_id)
    messages = build_messages(settings, history, user_text)
    parts: list[str] = []
    async for kind, text in stream_events(state, ctx, messages):
        if kind == "token":
            parts.append(text)
    reply = "".join(parts).strip() or "(sin respuesta)"
    state.store.add_turn(session_id, "user", user_text)
    state.store.add_turn(session_id, "assistant", reply)
    log_event(log, "chat", session=session_id, tools=ctx.tools_used, tainted=ctx.tainted)
    return reply, ctx


async def attach_speech(settings: Settings, text: str) -> dict:
    try:
        data, mime = await asyncio.to_thread(audio.synthesize, settings, text)
    except audio.TTSError as exc:
        return {"audio_error": str(exc)}
    return {"audio_base64": base64.b64encode(data).decode("ascii"), "audio_mime": mime}


# --- API compatible con OpenAI ---

def _split_content(content: Any) -> tuple[str, list[str]]:
    if content is None:
        return "", []
    if isinstance(content, str):
        return content, []
    texts: list[str] = []
    images: list[str] = []
    for part in content:
        kind = part.get("type")
        if kind in ("text", "input_text"):
            texts.append(str(part.get("text", "")))
        elif kind == "image_url":
            ref = part.get("image_url")
            url = ref.get("url", "") if isinstance(ref, dict) else str(ref or "")
            images.append(url)
    return "\n".join(t for t in texts if t), images


async def prepare_openai(state: State, req: ChatCompletionRequest) -> tuple[Ctx, list[dict]]:
    settings = state.settings
    system_parts: list[str] = []
    convo: list[tuple[str, str, list[str]]] = []
    for m in req.messages:
        text, images = _split_content(m.content)
        if m.role == "system":
            if text:
                system_parts.append(text)
        elif m.role in ("user", "assistant"):
            convo.append((m.role, text, images))
    last_user = max((i for i, c in enumerate(convo) if c[0] == "user"), default=None)
    if last_user is None:
        raise HTTPException(status_code=400, detail="Falta un mensaje de usuario")

    history: list[dict] = []
    for i, (role, text, images) in enumerate(convo[:last_user]):
        if images:
            text = (text + "\n[imagen adjunta]").strip()
        if text:
            history.append({"role": role, "content": text})
    history = history[-(settings.history_turns * 2):] if settings.history_turns > 0 else []

    _, user_text, images = convo[last_user]
    notes: list[str] = []
    for url in images[:3]:
        if url.startswith("data:"):
            raw = decode_image(url, settings.max_image_bytes)
            notes.append(await image_note(settings, raw))
        else:
            notes.append("[Imagen remota ignorada: solo se aceptan imágenes incrustadas]")
    user_text = "\n\n".join(x for x in [user_text, *notes] if x).strip() or "(mensaje vacío)"

    extra = "\n\n".join(system_parts)[:4000]
    ctx = Ctx(settings=settings, session_id="openai", extra_system=extra)
    return ctx, build_messages(settings, history, user_text, extra)


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def sse_stream(state: State, ctx: Ctx, messages: list[dict], model: str) -> AsyncIterator[str]:
    cid = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    def chunk(delta: dict, finish: str | None = None) -> str:
        return _sse(
            {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
        )

    yield chunk({"role": "assistant", "content": ""})
    try:
        async for kind, text in stream_events(state, ctx, messages):
            yield chunk({"content": f"\n*{text}*\n\n" if kind == "status" else text})
    except Exception as exc:
        log_event(log, "stream_error", error=exc.__class__.__name__)
        yield chunk({"content": f"\n\nError interno: {exc.__class__.__name__}"})
    yield chunk({}, "stop")
    yield "data: [DONE]\n\n"


# --- aplicación ---

def create_app(
    settings: Settings | None = None,
    runner: Runner | None = None,
    store: Store | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if not settings.agent_api_key.strip():
        raise RuntimeError("AGENT_API_KEY no está definida: el servidor no arranca sin API key.")
    setup_logging(settings.agent_debug)
    state = State(settings, store or Store(settings.agent_db_path), runner or default_runner(settings))
    auth = make_auth(settings)
    protected = [Depends(auth)]

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        state.store.close()

    app = FastAPI(title="IA-Local Agent", version="0.1.0", lifespan=lifespan)
    app.state.agent = state

    @app.get("/health")
    async def health() -> dict:
        alive = await asyncio.to_thread(llm.ollama_alive, settings)
        return {
            "status": "ok",
            "engine": settings.agent_engine,
            "model": settings.llm_model,
            "tools": sorted(s.name for s in enabled_tools(settings)),
            "ollama": alive,
        }

    @app.post("/chat", response_model=ChatResponse, dependencies=protected)
    async def chat(req: ChatRequest) -> ChatResponse:
        sid = req.session_id or uuid.uuid4().hex
        text = req.content
        if req.image_b64:
            raw = decode_image(req.image_b64, settings.max_image_bytes)
            text = f"{text}\n\n{await image_note(settings, raw)}"
        reply, ctx = await run_session(state, sid, text)
        extra = await attach_speech(settings, reply) if req.speak else {}
        return ChatResponse(response=reply, session_id=sid, tools_used=ctx.tools_used, **extra)

    @app.post("/audio-chat", response_model=AudioChatResponse, dependencies=protected)
    async def audio_chat(
        file: UploadFile = File(...),
        session_id: str | None = Form(None),
        speak: bool = Form(False),
    ) -> AudioChatResponse:
        if session_id is not None and not SESSION_RE.match(session_id):
            raise HTTPException(status_code=422, detail="session_id no válido")
        data = await read_upload(file, settings.max_audio_bytes)
        try:
            transcript = await asyncio.to_thread(audio.transcribe, settings, data)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"No se pudo transcribir el audio ({exc.__class__.__name__})")
        if not transcript:
            raise HTTPException(status_code=422, detail="No se detectó voz en el audio")
        sid = session_id or uuid.uuid4().hex
        reply, ctx = await run_session(state, sid, transcript[:20000])
        extra = await attach_speech(settings, reply) if speak else {}
        return AudioChatResponse(
            response=reply, session_id=sid, tools_used=ctx.tools_used, transcript=transcript, **extra
        )

    if settings.enable_screen:

        @app.post("/screenshot", dependencies=protected)
        async def screenshot() -> Response:
            try:
                img = await asyncio.to_thread(vision.capture_screen)
                jpg = await asyncio.to_thread(vision.to_jpeg_bytes, img)
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"No se pudo capturar la pantalla ({exc})")
            return Response(content=jpg, media_type="image/jpeg")

    @app.get("/search", dependencies=protected)
    async def search(q: str = Query(..., min_length=1, max_length=300)) -> dict:
        if not settings.enable_web:
            raise HTTPException(status_code=404, detail="Búsqueda web desactivada")
        try:
            results = await asyncio.to_thread(web._search_text, q, 8, "")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Fallo en el buscador ({exc.__class__.__name__})")
        return {"query": q, "results": results}

    @app.get("/v1/models", dependencies=protected)
    async def list_models() -> dict:
        return {
            "object": "list",
            "data": [{"id": MODEL_ID, "object": "model", "created": 0, "owned_by": "ia-local"}],
        }

    @app.post("/v1/chat/completions", dependencies=protected)
    async def chat_completions(req: ChatCompletionRequest):
        ctx, messages = await prepare_openai(state, req)
        model = req.model or MODEL_ID
        if req.stream:
            return StreamingResponse(
                sse_stream(state, ctx, messages, model),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        parts: list[str] = []
        async for kind, text in stream_events(state, ctx, messages):
            if kind == "token":
                parts.append(text)
        log_event(log, "completion", tools=ctx.tools_used, tainted=ctx.tainted)
        return JSONResponse(
            {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "".join(parts).strip() or "(sin respuesta)"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        )

    @app.post("/v1/audio/transcriptions", dependencies=protected)
    async def transcriptions(
        file: UploadFile = File(...),
        model: str | None = Form(None),
        language: str | None = Form(None),
    ) -> dict:
        data = await read_upload(file, settings.max_audio_bytes)
        lang = language if language and LANG_RE.match(language) else None
        try:
            text = await asyncio.to_thread(audio.transcribe, settings, data, lang)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"No se pudo transcribir el audio ({exc.__class__.__name__})")
        return {"text": text}

    @app.post("/v1/audio/speech", dependencies=protected)
    async def speech(req: SpeechRequest) -> Response:
        try:
            data, mime = await asyncio.to_thread(audio.synthesize, settings, req.input)
        except audio.TTSError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return Response(content=data, media_type=mime)

    return app


def main() -> None:
    settings = get_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.agent_host, port=settings.agent_port, log_level="warning")


if __name__ == "__main__":
    main()
