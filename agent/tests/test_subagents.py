import threading
import time

from pydantic import BaseModel

from agent import llm
from agent.registry import Ctx, ToolSpec, enabled_tools
from agent.tests.test_engine import EchoArgs, _call, _collect, _text
from agent.tools import agents


def test_delegate_solo_si_esta_activado(make_settings):
    assert "delegate" in {s.name for s in enabled_tools(make_settings())}
    assert "delegate" not in {s.name for s in enabled_tools(make_settings(enable_subagents=False))}


def test_subagentes_sin_herramientas_peligrosas_ni_delegate(make_settings):
    names = {s.name for s in agents._subagent_specs(Ctx(settings=make_settings(enable_code=True)))}
    assert "delegate" not in names
    assert "run_python" not in names and "write_file" not in names and "run_command" not in names
    assert "web_search" in names


def test_delegate_reparte_tareas_y_devuelve_resultados(make_settings, monkeypatch):
    s = make_settings()
    vistos = []

    async def fake(settings, messages, tools=None, model=None, fmt=None):
        vistos.append((messages, tools, settings.max_iterations))
        tarea = messages[-1]["content"]
        yield _text(f"resultado de {tarea}")

    monkeypatch.setattr(llm, "chat_stream", fake)
    ctx = Ctx(settings=s)
    out = agents._delegate(ctx, agents.DelegateArgs(tasks=["uno", "dos"]))

    assert "resultado de uno" in out and "resultado de dos" in out
    assert out.index("uno") < out.index("dos")
    assert 'n="2" untrusted' in out
    assert len(vistos) == 2
    assert all(it == s.subagent_iterations for _, _, it in vistos)
    nombres = {t["function"]["name"] for t in vistos[0][1]}
    assert "delegate" not in nombres
    assert "subagente" in vistos[0][0][0]["content"]


def test_delegate_recorta_a_max_subagentes(make_settings, monkeypatch):
    s = make_settings(max_subagents=2)

    async def fake(settings, messages, tools=None, model=None, fmt=None):
        yield _text("ok")

    monkeypatch.setattr(llm, "chat_stream", fake)
    out = agents._delegate(Ctx(settings=s), agents.DelegateArgs(tasks=["a", "b", "c"]))
    assert out.count("<subagent_result") == 2


def test_fallo_de_un_subagente_no_tumba_a_los_demas(make_settings, monkeypatch):
    s = make_settings()

    async def fake(settings, messages, tools=None, model=None, fmt=None):
        if messages[-1]["content"] == "mala":
            raise RuntimeError("boom")
        yield _text("bien")

    monkeypatch.setattr(llm, "chat_stream", fake)
    out = agents._delegate(Ctx(settings=s), agents.DelegateArgs(tasks=["mala", "buena"]))
    assert "ERROR: el subagente falló" in out and "bien" in out


def test_el_taint_de_un_subagente_llega_al_agente_principal(make_settings, monkeypatch):
    s = make_settings()
    web = ToolSpec("web_x", "w", EchoArgs, lambda c, a: "contenido", "web", taints=True)
    monkeypatch.setattr(agents, "_subagent_specs", lambda ctx: [web])
    estado = {"n": 0}

    async def fake(settings, messages, tools=None, model=None, fmt=None):
        estado["n"] += 1
        if estado["n"] == 1:
            yield _call("web_x", {})
        else:
            yield _text("listo")

    monkeypatch.setattr(llm, "chat_stream", fake)
    ctx = Ctx(settings=s)
    agents._delegate(ctx, agents.DelegateArgs(tasks=["busca"]))
    assert ctx.tainted and "web_x" in ctx.tools_used


def test_el_agente_principal_puede_llamar_a_delegate(make_settings, monkeypatch):
    s = make_settings()
    estado = {"n": 0}

    async def fake(settings, messages, tools=None, model=None, fmt=None):
        estado["n"] += 1
        if estado["n"] == 1:
            yield _call("delegate", {"tasks": ["x", "y"]})
        elif tools is not None and estado["n"] == 2 or estado["n"] == 3:
            yield _text("sub")
        else:
            yield _text("final")

    monkeypatch.setattr(llm, "chat_stream", fake)
    events = _collect(s, Ctx(settings=s), [sp for sp in enabled_tools(s) if sp.name == "delegate"])
    assert ("status", "Usando delegate\u2026") in events


def test_herramientas_seguras_de_un_mismo_paso_corren_en_paralelo(make_settings, monkeypatch):
    s = make_settings()
    activas, pico, lock = [0], [0], threading.Lock()

    def lenta(ctx, a):
        with lock:
            activas[0] += 1
            pico[0] = max(pico[0], activas[0])
        time.sleep(0.2)
        with lock:
            activas[0] -= 1
        return f"r{a.text}"

    specs = [ToolSpec("lenta", "l", EchoArgs, lenta, "files")]
    estado = {"n": 0}
    seen = []

    async def fake(settings, messages, tools=None, model=None, fmt=None):
        seen.append([dict(m) for m in messages])
        estado["n"] += 1
        if estado["n"] == 1:
            yield {"message": {"content": "", "tool_calls": [
                {"function": {"name": "lenta", "arguments": {"text": "a"}}},
                {"function": {"name": "lenta", "arguments": {"text": "b"}}},
            ]}}
        else:
            yield _text("fin")

    monkeypatch.setattr(llm, "chat_stream", fake)
    inicio = time.monotonic()
    _collect(s, Ctx(settings=s), specs)
    assert pico[0] == 2
    assert time.monotonic() - inicio < 0.38
    tools = [m["content"] for m in seen[1] if m["role"] == "tool"]
    assert tools == ["ra", "rb"]


def test_las_peligrosas_van_despues_de_las_seguras_en_el_mismo_paso(make_settings, monkeypatch):
    s = make_settings()
    orden = []
    specs = [
        ToolSpec("danger", "d", EchoArgs, lambda c, a: orden.append("danger") or "d", "files", dangerous=True),
        ToolSpec("web_x", "w", EchoArgs, lambda c, a: orden.append("web") or "w", "web", taints=True),
    ]
    estado = {"n": 0}
    seen = []

    async def fake(settings, messages, tools=None, model=None, fmt=None):
        seen.append([dict(m) for m in messages])
        estado["n"] += 1
        if estado["n"] == 1:
            yield {"message": {"content": "", "tool_calls": [
                {"function": {"name": "danger", "arguments": {}}},
                {"function": {"name": "web_x", "arguments": {}}},
            ]}}
        else:
            yield _text("fin")

    monkeypatch.setattr(llm, "chat_stream", fake)
    _collect(s, Ctx(settings=s), specs)
    assert orden == ["web"]
    tools = [m for m in seen[1] if m["role"] == "tool"]
    assert [m["tool_name"] for m in tools] == ["danger", "web_x"]
    assert tools[0]["content"].startswith("BLOQUEADO")
