from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from agent.config import Settings
from agent.utils.logger import get_logger, log_event

MAX_TOOL_CHARS = 16000
log = get_logger("registry")


@dataclass
class Ctx:
    """Estado de una petición; lo comparten los motores y todas las herramientas."""

    settings: Settings
    session_id: str = ""
    tainted: bool = False
    tools_used: list[str] = field(default_factory=list)
    extra_system: str = ""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    func: Callable[[Ctx, Any], str]
    group: str  # web | files | code | screen | memory
    role: str = "executor"  # research | vision | executor (CrewAI)
    dangerous: bool = False
    taints: bool = False


GROUP_FLAGS = {
    "web": "enable_web",
    "files": "enable_files",
    "code": "enable_code",
    "screen": "enable_screen",
    "memory": "enable_memory",
}


def all_tools() -> dict[str, ToolSpec]:
    from agent.tools import code, memory, system, vision, web

    out: dict[str, ToolSpec] = {}
    for module in (web, system, vision, code, memory):
        for spec in module.SPECS:
            out[spec.name] = spec
    return out


def enabled_tools(settings: Settings) -> list[ToolSpec]:
    return [t for t in all_tools().values() if getattr(settings, GROUP_FLAGS[t.group], False)]


def _strip_titles(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


def ollama_schema(spec: ToolSpec) -> dict:
    schema = spec.args_model.model_json_schema()
    props = {k: _strip_titles(v) for k, v in schema.get("properties", {}).items()}
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": {"type": "object", "properties": props, "required": schema.get("required", [])},
        },
    }


def run_spec(spec: ToolSpec, raw_args: Any, ctx: Ctx) -> str:
    """Valida argumentos, aplica el taint lock y ejecuta la herramienta."""
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except json.JSONDecodeError:
            raw_args = {}
    if not isinstance(raw_args, dict):
        raw_args = {}
    try:
        args = spec.args_model.model_validate(raw_args)
    except ValidationError as exc:
        detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return f"ERROR: argumentos inválidos para {spec.name}: {detail}"
    if spec.dangerous and ctx.settings.taint_lock and ctx.tainted:
        return (
            f"BLOQUEADO: {spec.name} no se puede usar en este turno porque ya se ha leído contenido "
            "web o de pantalla. Dile al usuario que repita la orden en un mensaje nuevo si quiere continuar."
        )
    if spec.taints:
        ctx.tainted = True
    if spec.name not in ctx.tools_used:
        ctx.tools_used.append(spec.name)
    try:
        out = spec.func(ctx, args)
    except Exception as exc:  # la herramienta no debe tumbar el bucle del agente
        log_event(log, "tool_error", tool=spec.name, error=exc.__class__.__name__)
        out = f"ERROR: {spec.name} falló ({exc.__class__.__name__}: {exc})"
    out = str(out)
    if len(out) > MAX_TOOL_CHARS:
        out = out[:MAX_TOOL_CHARS] + "\n[... recortado]"
    return out


def run_tool(by_name: dict[str, ToolSpec], name: str, raw_args: Any, ctx: Ctx) -> str:
    spec = by_name.get(name)
    if spec is None:
        return f"ERROR: herramienta desconocida o desactivada: {name}"
    return run_spec(spec, raw_args, ctx)
