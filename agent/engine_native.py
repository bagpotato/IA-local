from __future__ import annotations

import asyncio
from typing import AsyncIterator

from agent import llm
from agent.config import Settings
from agent.registry import Ctx, ToolSpec, enabled_tools, ollama_schema, run_tool

Event = tuple[str, str]
MAX_CALLS_PER_STEP = 4


async def run_native(
    settings: Settings,
    ctx: Ctx,
    messages: list[dict],
    specs: list[ToolSpec] | None = None,
) -> AsyncIterator[Event]:
    """Bucle de tool-calling directo contra Ollama. Emite ("status", texto) y ("token", texto)."""
    specs = enabled_tools(settings) if specs is None else specs
    by_name = {s.name: s for s in specs}
    schemas = [ollama_schema(s) for s in specs] or None
    msgs = list(messages)

    for _ in range(settings.max_iterations):
        parts: list[str] = []
        calls: list[dict] = []
        try:
            async for chunk in llm.chat_stream(settings, msgs, schemas):
                msg = chunk.get("message") or {}
                text = msg.get("content") or ""
                if text:
                    parts.append(text)
                    yield ("token", text)
                calls.extend(msg.get("tool_calls") or [])
        except llm.ToolsNotSupported as exc:
            yield ("token", str(exc))
            return
        except llm.OllamaError as exc:
            yield ("token", f"Error del modelo: {exc}")
            return

        if not calls:
            return

        msgs.append({"role": "assistant", "content": "".join(parts), "tool_calls": calls})
        for call in calls[:MAX_CALLS_PER_STEP]:
            fn = call.get("function") or {}
            name = str(fn.get("name", ""))
            yield ("status", f"Usando {name}…")
            result = await asyncio.to_thread(run_tool, by_name, name, fn.get("arguments"), ctx)
            msgs.append({"role": "tool", "tool_name": name, "content": result})

    # Iteraciones agotadas: respuesta final sin herramientas
    msgs.append(
        {
            "role": "user",
            "content": "Has alcanzado el máximo de pasos. Responde ahora al usuario con la información reunida, "
            "sin llamar a más herramientas.",
        }
    )
    try:
        async for chunk in llm.chat_stream(settings, msgs, None):
            text = (chunk.get("message") or {}).get("content") or ""
            if text:
                yield ("token", text)
    except llm.OllamaError as exc:
        yield ("token", f"Error del modelo: {exc}")
