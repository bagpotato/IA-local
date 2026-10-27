from __future__ import annotations

import asyncio
from typing import AsyncIterator

from agent import llm
from agent.config import Settings
from agent.registry import Ctx, ToolSpec, enabled_tools, ollama_schema, run_tool

Event = tuple[str, str]
MAX_CALLS_PER_STEP = 4


async def _run_batch(by_name: dict[str, ToolSpec], batch: list[tuple[str, object]], ctx: Ctx) -> list[str]:
    """Herramientas seguras en paralelo; las peligrosas después y de una en una (el taint lock depende del orden)."""
    results: list[str] = [""] * len(batch)

    def dangerous(name: str) -> bool:
        spec = by_name.get(name)
        return bool(spec and spec.dangerous)

    safe = [i for i, (n, _) in enumerate(batch) if not dangerous(n)]
    outs = await asyncio.gather(*(asyncio.to_thread(run_tool, by_name, batch[i][0], batch[i][1], ctx) for i in safe))
    for i, out in zip(safe, outs):
        results[i] = out
    for i, (n, a) in enumerate(batch):
        if dangerous(n):
            results[i] = await asyncio.to_thread(run_tool, by_name, n, a, ctx)
    return results


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
        batch = [(str((c.get("function") or {}).get("name", "")), (c.get("function") or {}).get("arguments"))
                 for c in calls[:MAX_CALLS_PER_STEP]]
        for name, _ in batch:
            yield ("status", f"Usando {name}…")
        results = await _run_batch(by_name, batch, ctx)
        for (name, _), result in zip(batch, results):
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
