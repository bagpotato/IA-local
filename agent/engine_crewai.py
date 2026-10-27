import asyncio
import json
import os
from typing import Any, AsyncIterator, Type

from pydantic import BaseModel

from agent import llm, prompts
from agent.config import Settings
from agent.registry import Ctx, ToolSpec, enabled_tools, run_spec

Event = tuple[str, str]
ROLES = ("research", "vision", "executor")


def _disable_telemetry() -> None:
    os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")


def make_crewai_tool(spec: ToolSpec, ctx: Ctx):
    """Envuelve una entrada del registro como herramienta CrewAI compartiendo el Ctx de la petición."""
    from crewai.tools import BaseTool

    class RegistryTool(BaseTool):
        name: str = spec.name
        description: str = spec.description
        args_schema: Type[BaseModel] = spec.args_model

        def _run(self, **kwargs: Any) -> str:
            return run_spec(spec, kwargs, ctx)

    return RegistryTool()


def _transcript(messages: list[dict]) -> tuple[str, str]:
    """Devuelve (conversación previa en texto, último mensaje del usuario)."""
    convo = [m for m in messages if m.get("role") in ("user", "assistant")]
    last = ""
    for m in reversed(convo):
        if m["role"] == "user":
            last = str(m.get("content", ""))
            break
    prev = convo[:-1] if convo and convo[-1]["role"] == "user" else convo
    text = "\n".join(f"{m['role']}: {str(m.get('content', ''))[:1500]}" for m in prev[-8:])
    return text, last


def _system_text(messages: list[dict]) -> str:
    return "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")


async def route(settings: Settings, messages: list[dict], available: set[str]) -> list[str]:
    """Paso router: Ollama con format=json decide qué roles hacen falta."""
    history, last = _transcript(messages)
    ask = [
        {"role": "system", "content": prompts.router_prompt(available)},
        {"role": "user", "content": (f"Conversación previa:\n{history}\n\n" if history else "") + f"Mensaje: {last}"},
    ]
    try:
        data = await llm.chat_once(settings, ask, model=settings.router_model_name, fmt="json")
        parsed = json.loads((data.get("message") or {}).get("content", "{}"))
        roles = parsed.get("roles", []) if isinstance(parsed, dict) else []
    except (llm.OllamaError, json.JSONDecodeError, AttributeError, TypeError):
        return [r for r in ROLES if r in available]
    return [r for r in ROLES if r in roles and r in available]


def _kickoff(settings: Settings, ctx: Ctx, messages: list[dict], roles: list[str], specs: list[ToolSpec]) -> str:
    _disable_telemetry()
    from crewai import LLM, Agent, Crew, Process, Task

    model = LLM(model=f"ollama/{settings.llm_model}", base_url=settings.ollama_host)
    history, last = _transcript(messages)
    context = _system_text(messages)
    brief = (f"Contexto del sistema:\n{context}\n\n" if context else "") + (
        f"Conversación previa:\n{history}\n\n" if history else ""
    )

    agents: list[Any] = []
    tasks: list[Any] = []
    for role in roles:
        info = prompts.CREW_ROLES[role]
        tools = [make_crewai_tool(s, ctx) for s in specs if s.role == role]
        agent = Agent(
            role=info["role"],
            goal=info["goal"],
            backstory=prompts.ROLE_BACKSTORY[role],
            llm=model,
            tools=tools,
            allow_delegation=False,
            verbose=settings.agent_debug,
        )
        agents.append(agent)
        tasks.append(
            Task(
                description=f"{brief}Petición del usuario: {last}\n\nHaz la parte que te corresponde como {info['role']}.",
                expected_output=info["expected"],
                agent=agent,
            )
        )

    writer = agents[-1]
    tasks.append(
        Task(
            description=(
                f"{prompts.build_system_prompt({s.name for s in specs})}\n\n"
                f"Petición original del usuario: {last}\n\n"
                "Redacta la respuesta final usando solo lo obtenido en las tareas anteriores. "
                "Respóndele en su mismo idioma, cita con [n] y termina con la lista 'Fuentes:' si hubo fuentes web. "
                "Si algo no se encontró, dilo."
            ),
            expected_output="Respuesta final para el usuario, con citas [n] y lista de Fuentes si procede",
            agent=writer,
            context=list(tasks),
        )
    )
    crew = Crew(agents=agents, tasks=tasks, process=Process.sequential, verbose=settings.agent_debug)
    result = crew.kickoff()
    return str(getattr(result, "raw", None) or result)


async def run_crewai(
    settings: Settings,
    ctx: Ctx,
    messages: list[dict],
    specs: list[ToolSpec] | None = None,
) -> AsyncIterator[Event]:
    specs = enabled_tools(settings) if specs is None else specs
    specs = [s for s in specs if s.group != "agents"]
    available = {s.role for s in specs}
    roles = await route(settings, messages, available) if available else []

    if not roles:
        try:
            data = await llm.chat_once(settings, messages)
        except llm.OllamaError as exc:
            yield ("token", f"Error del modelo: {exc}")
            return
        yield ("token", str((data.get("message") or {}).get("content", "")))
        return

    yield ("status", "Equipo: " + ", ".join(roles) + "…")
    try:
        text = await asyncio.to_thread(_kickoff, settings, ctx, messages, roles, specs)
    except ImportError:
        yield ("token", "CrewAI no está instalado. Ejecuta: pip install -r requirements-crewai.txt")
        return
    except Exception as exc:
        yield ("token", f"Error en CrewAI: {exc.__class__.__name__}: {exc}")
        return
    yield ("token", text)
