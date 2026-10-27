from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from agent.prompts import build_system_prompt
from agent.registry import Ctx, ToolSpec

MAX_RESULT_CHARS = 4000

SUBAGENT_RULES = (
    "Eres un subagente: otro agente te delega una tarea concreta e independiente. Haz solo esa tarea con las "
    "herramientas que tienes y termina con un resultado breve y verificable (datos, cifras y fuentes si las hay), "
    "sin saludos ni explicaciones de lo que has hecho. No tienes herramientas peligrosas ni puedes delegar."
)


class DelegateArgs(BaseModel):
    tasks: list[str] = Field(
        ...,
        min_length=1,
        max_length=8,
        description="Tareas independientes y autocontenidas; cada una la resuelve un subagente en paralelo",
    )


def _subagent_specs(ctx: Ctx) -> list[ToolSpec]:
    from agent.registry import enabled_tools

    return [s for s in enabled_tools(ctx.settings) if s.group != "agents" and not s.dangerous]


async def _run_one(ctx: Ctx, specs: list[ToolSpec], task: str, sem: asyncio.Semaphore) -> str:
    from agent.engine_native import run_native

    system = build_system_prompt({s.name for s in specs}) + "\n\n" + SUBAGENT_RULES
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    settings = ctx.settings.model_copy(update={"max_iterations": ctx.settings.subagent_iterations})
    parts: list[str] = []
    async with sem:
        try:
            async for kind, text in run_native(settings, ctx, messages, specs):
                if kind == "token":
                    parts.append(text)
        except Exception as exc:
            return f"ERROR: el subagente falló ({exc.__class__.__name__}: {exc})"
    out = "".join(parts).strip() or "(sin resultado)"
    if len(out) > MAX_RESULT_CHARS:
        out = out[:MAX_RESULT_CHARS] + "\n[... recortado]"
    return out


async def _run_all(ctx: Ctx, tasks: list[str]) -> list[str]:
    specs = _subagent_specs(ctx)
    sem = asyncio.Semaphore(ctx.settings.subagent_concurrency)
    return await asyncio.gather(*(_run_one(ctx, specs, t, sem) for t in tasks))


def _delegate(ctx: Ctx, a: DelegateArgs) -> str:
    tasks = [t.strip() for t in a.tasks if t.strip()][: ctx.settings.max_subagents]
    if not tasks:
        return "ERROR: no hay tareas que delegar."
    results = asyncio.run(_run_all(ctx, tasks))
    blocks = [
        f'<subagent_result n="{i}" untrusted>\nTarea: {t}\n{r}\n</subagent_result>'
        for i, (t, r) in enumerate(zip(tasks, results), 1)
    ]
    return "\n\n".join(blocks)


SPECS = [
    ToolSpec(
        "delegate",
        "Reparte tareas independientes entre subagentes que trabajan en paralelo y devuelve el resultado de cada uno. "
        "Úsala cuando la petición se divida en 2 o más partes que no dependen entre sí (investigar varios temas, "
        "comparar opciones, revisar varios ficheros). No la uses para preguntas simples ni para pasos que dependen "
        "del anterior. Cada tarea debe ser autocontenida: el subagente no ve la conversación.",
        DelegateArgs, _delegate, "agents", "executor",
    ),
]
