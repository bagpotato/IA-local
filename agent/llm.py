from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from agent.config import Settings


class OllamaError(Exception):
    pass


class ToolsNotSupported(OllamaError):
    pass


def _payload(settings: Settings, messages: list[dict], tools: list[dict] | None, model: str | None,
             stream: bool, fmt: str | None) -> dict:
    payload: dict[str, Any] = {
        "model": model or settings.llm_model,
        "messages": messages,
        "stream": stream,
        "options": {"num_ctx": settings.num_ctx, "temperature": 0.2},
    }
    if tools:
        payload["tools"] = tools
    if fmt:
        payload["format"] = fmt
    return payload


def _raise_for_body(status: int, body: str, model: str) -> None:
    msg = body
    try:
        msg = str(json.loads(body).get("error", body))
    except (json.JSONDecodeError, AttributeError):
        pass
    low = msg.lower()
    if status == 400 and "support" in low and "tool" in low:
        raise ToolsNotSupported(
            f"El modelo '{model}' no soporta herramientas (tool-calling). "
            "Elige en LLM_MODEL un modelo con soporte de tool-calling."
        )
    raise OllamaError(f"Ollama respondió {status}: {msg[:300]}")


def _url(settings: Settings, path: str) -> str:
    return f"{settings.ollama_host}{path}"


async def chat_stream(
    settings: Settings,
    messages: list[dict],
    tools: list[dict] | None = None,
    model: str | None = None,
    fmt: str | None = None,
) -> AsyncIterator[dict]:
    name = model or settings.llm_model
    timeout = httpx.Timeout(settings.request_timeout, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            async with client.stream(
                "POST", _url(settings, "/api/chat"), json=_payload(settings, messages, tools, name, True, fmt)
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    _raise_for_body(resp.status_code, body, name)
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "error" in obj:
                        raise OllamaError(str(obj["error"]))
                    yield obj
                    if obj.get("done"):
                        break
    except httpx.ConnectError as exc:
        raise OllamaError(f"No se pudo conectar con Ollama en {settings.ollama_host}") from exc
    except httpx.TimeoutException as exc:
        raise OllamaError("Ollama tardó demasiado en responder") from exc
    except httpx.HTTPError as exc:
        raise OllamaError(f"Error de red con Ollama: {exc.__class__.__name__}") from exc


async def chat_once(
    settings: Settings,
    messages: list[dict],
    model: str | None = None,
    fmt: str | None = None,
    tools: list[dict] | None = None,
) -> dict:
    name = model or settings.llm_model
    timeout = httpx.Timeout(settings.request_timeout, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.post(
                _url(settings, "/api/chat"), json=_payload(settings, messages, tools, name, False, fmt)
            )
    except httpx.ConnectError as exc:
        raise OllamaError(f"No se pudo conectar con Ollama en {settings.ollama_host}") from exc
    except httpx.TimeoutException as exc:
        raise OllamaError("Ollama tardó demasiado en responder") from exc
    if resp.status_code != 200:
        _raise_for_body(resp.status_code, resp.text, name)
    data = resp.json()
    if "error" in data:
        raise OllamaError(str(data["error"]))
    return data


def chat_sync(settings: Settings, messages: list[dict], model: str | None = None, fmt: str | None = None) -> dict:
    name = model or settings.llm_model
    timeout = httpx.Timeout(settings.request_timeout, connect=10.0)
    try:
        resp = httpx.post(
            _url(settings, "/api/chat"),
            json=_payload(settings, messages, None, name, False, fmt),
            timeout=timeout,
            trust_env=False,
        )
    except httpx.ConnectError as exc:
        raise OllamaError(f"No se pudo conectar con Ollama en {settings.ollama_host}") from exc
    except httpx.TimeoutException as exc:
        raise OllamaError("Ollama tardó demasiado en responder") from exc
    if resp.status_code != 200:
        _raise_for_body(resp.status_code, resp.text, name)
    data = resp.json()
    if "error" in data:
        raise OllamaError(str(data["error"]))
    return data


def vision_describe(settings: Settings, image_b64: str, prompt: str) -> str:
    data = chat_sync(
        settings,
        [{"role": "user", "content": prompt, "images": [image_b64]}],
        model=settings.vision_model,
    )
    return str((data.get("message") or {}).get("content", "")).strip()


def embed_sync(settings: Settings, text: str) -> list[float]:
    try:
        resp = httpx.post(
            _url(settings, "/api/embed"),
            json={"model": settings.embed_model, "input": text},
            timeout=httpx.Timeout(60.0, connect=10.0),
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        raise OllamaError(f"No se pudo obtener el embedding: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        _raise_for_body(resp.status_code, resp.text, settings.embed_model)
    embeddings = resp.json().get("embeddings") or []
    if not embeddings:
        raise OllamaError("Ollama no devolvió embeddings")
    return [float(x) for x in embeddings[0]]


def ollama_alive(settings: Settings, timeout: float = 2.0) -> bool:
    try:
        return httpx.get(_url(settings, "/api/tags"), timeout=timeout, trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False
