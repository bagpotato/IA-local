import asyncio

from pydantic import BaseModel

from agent import llm
from agent.engine_native import run_native
from agent.registry import Ctx, ToolSpec


class EchoArgs(BaseModel):
    text: str = "x"


def _fake_stream(script, seen=None):
    """script: lista de respuestas; cada una es la lista de chunks de un paso."""
    state = {"n": 0}

    async def fake(settings, messages, tools=None, model=None, fmt=None):
        if seen is not None:
            seen.append({"messages": [dict(m) for m in messages], "tools": tools})
        step = script[min(state["n"], len(script) - 1)]
        state["n"] += 1
        for chunk in step:
            yield chunk

    return fake


def _call(name, args):
    return {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}


def _text(t):
    return {"message": {"role": "assistant", "content": t}}


def _collect(settings, ctx, specs, messages=None):
    async def go():
        out = []
        async for ev in run_native(settings, ctx, messages or [{"role": "user", "content": "hola"}], specs):
            out.append(ev)
        return out

    return asyncio.run(go())


def test_ejecuta_herramienta_y_responde(make_settings, monkeypatch):
    s = make_settings()
    ran = []

    def echo(ctx, a):
        ran.append(a.text)
        return f"eco: {a.text}"

    specs = [ToolSpec("echo", "repite", EchoArgs, echo, "files")]
    seen = []
    monkeypatch.setattr(llm, "chat_stream", _fake_stream([[_call("echo", {"text": "hola"})], [_text("Listo "), _text("ya")]], seen))
    ctx = Ctx(settings=s)
    events = _collect(s, ctx, specs)

    assert ran == ["hola"]
    assert ("status", "Usando echo…") in events
    assert "".join(t for k, t in events if k == "token") == "Listo ya"
    assert ctx.tools_used == ["echo"]
    # el segundo paso recibe el resultado de la herramienta
    tool_msgs = [m for m in seen[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["content"] == "eco: hola"
    assert seen[0]["tools"][0]["function"]["name"] == "echo"


def test_taint_lock_bloquea_dangerous_tras_web(make_settings, monkeypatch):
    s = make_settings()
    ejecutadas = []

    def lee_web(ctx, a):
        ejecutadas.append("web")
        return "contenido web"

    def peligrosa(ctx, a):
        ejecutadas.append("peligrosa")
        return "hecho"

    specs = [
        ToolSpec("web_x", "web", EchoArgs, lee_web, "web", taints=True),
        ToolSpec("danger", "peligrosa", EchoArgs, peligrosa, "files", dangerous=True),
    ]
    seen = []
    monkeypatch.setattr(
        llm,
        "chat_stream",
        _fake_stream([[_call("web_x", {})], [_call("danger", {})], [_text("fin")]], seen),
    )
    ctx = Ctx(settings=s)
    _collect(s, ctx, specs)

    assert ejecutadas == ["web"]
    assert ctx.tainted
    bloqueo = [m for m in seen[2]["messages"] if m["role"] == "tool" and m["tool_name"] == "danger"]
    assert bloqueo and bloqueo[0]["content"].startswith("BLOQUEADO")


def test_sin_taint_lock_la_dangerous_se_ejecuta(make_settings, monkeypatch):
    s = make_settings(taint_lock=False)
    ejecutadas = []
    specs = [
        ToolSpec("web_x", "web", EchoArgs, lambda c, a: "w", "web", taints=True),
        ToolSpec("danger", "p", EchoArgs, lambda c, a: ejecutadas.append(1) or "ok", "files", dangerous=True),
    ]
    monkeypatch.setattr(llm, "chat_stream", _fake_stream([[_call("web_x", {})], [_call("danger", {})], [_text("fin")]]))
    _collect(s, Ctx(settings=s), specs)
    assert ejecutadas == [1]


def test_dangerous_sin_web_previa_se_ejecuta(make_settings, monkeypatch):
    s = make_settings()
    ejecutadas = []
    specs = [ToolSpec("danger", "p", EchoArgs, lambda c, a: ejecutadas.append(1) or "ok", "files", dangerous=True)]
    monkeypatch.setattr(llm, "chat_stream", _fake_stream([[_call("danger", {})], [_text("fin")]]))
    _collect(s, Ctx(settings=s), specs)
    assert ejecutadas == [1]


def test_herramienta_desconocida_no_rompe_el_bucle(make_settings, monkeypatch):
    s = make_settings()
    seen = []
    monkeypatch.setattr(llm, "chat_stream", _fake_stream([[_call("no_existe", {})], [_text("ok")]], seen))
    events = _collect(s, Ctx(settings=s), [])
    assert "".join(t for k, t in events if k == "token") == "ok"
    assert any(m["role"] == "tool" and m["content"].startswith("ERROR") for m in seen[1]["messages"])


def test_agota_iteraciones_y_pide_respuesta_final(make_settings, monkeypatch):
    s = make_settings(max_iterations=2)
    specs = [ToolSpec("echo", "e", EchoArgs, lambda c, a: "r", "files")]
    seen = []

    async def fake(settings, messages, tools=None, model=None, fmt=None):
        seen.append(tools)
        if tools is None:
            yield _text("respuesta final")
        else:
            yield _call("echo", {})

    monkeypatch.setattr(llm, "chat_stream", fake)
    events = _collect(s, Ctx(settings=s), specs)
    assert seen[:2] != [None, None] and seen[-1] is None
    assert len(seen) == 3
    assert "".join(t for k, t in events if k == "token") == "respuesta final"


def test_modelo_sin_soporte_de_tools(make_settings, monkeypatch):
    s = make_settings()

    async def fake(settings, messages, tools=None, model=None, fmt=None):
        raise llm.ToolsNotSupported("El modelo 'x' no soporta herramientas")
        yield  # pragma: no cover

    monkeypatch.setattr(llm, "chat_stream", fake)
    events = _collect(s, Ctx(settings=s), [])
    assert events and "no soporta" in events[0][1]


def test_error_a_mitad_de_stream(make_settings, monkeypatch):
    s = make_settings()

    async def fake(settings, messages, tools=None, model=None, fmt=None):
        yield _text("parcial")
        raise llm.OllamaError("modelo caído")

    monkeypatch.setattr(llm, "chat_stream", fake)
    events = _collect(s, Ctx(settings=s), [])
    texto = "".join(t for k, t in events if k == "token")
    assert "parcial" in texto and "modelo caído" in texto


def test_error_400_de_tools_se_traduce():
    err = llm.OllamaError
    # el cuerpo de error 400 por falta de tools se traduce a ToolsNotSupported
    try:
        llm._raise_for_body(400, '{"error":"registry.ollama.ai/library/x does not support tools"}', "x")
    except llm.ToolsNotSupported as exc:
        assert "x" in str(exc)
    else:
        raise AssertionError("debía lanzar ToolsNotSupported")
    try:
        llm._raise_for_body(500, "boom", "x")
    except err as exc:
        assert not isinstance(exc, llm.ToolsNotSupported)
