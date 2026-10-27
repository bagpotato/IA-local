import json

import pytest
from fastapi.testclient import TestClient

from agent import llm
from agent.main import create_app
from agent.storage.db import Store

KEY = "clave-de-prueba"
AUTH = {"Authorization": f"Bearer {KEY}"}


@pytest.fixture
def seen():
    return []


@pytest.fixture
def client(make_settings, monkeypatch, seen):
    monkeypatch.setattr(llm, "ollama_alive", lambda settings, timeout=2.0: False)

    async def runner(settings, ctx, messages):
        seen.append(messages)
        yield ("status", "Usando prueba…")
        yield ("token", "Hola ")
        yield ("token", "mundo")

    app = create_app(make_settings(), runner=runner, store=Store(":memory:"))
    with TestClient(app) as c:
        yield c


def test_health_es_publico(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["ollama"] is False
    assert "web_research" in body["tools"] and body["engine"] == "native"


def test_sin_clave_no_arranca(make_settings):
    with pytest.raises(RuntimeError):
        create_app(make_settings(agent_api_key=""), runner=lambda *a: None)


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("post", "/chat", {"content": "hola"}),
        ("get", "/v1/models", None),
        ("post", "/v1/chat/completions", {"messages": [{"role": "user", "content": "hola"}]}),
        ("get", "/search?q=hola", None),
        ("post", "/v1/audio/speech", {"input": "hola"}),
    ],
)
def test_401_sin_clave_o_con_clave_incorrecta(client, method, path, payload):
    call = getattr(client, method)
    kw = {"json": payload} if payload is not None else {}
    assert call(path, **kw).status_code == 401
    assert call(path, headers={"Authorization": "Bearer otra"}, **kw).status_code == 401
    assert call(path, headers={"X-API-Key": "otra"}, **kw).status_code == 401


def test_chat_con_bearer_y_con_x_api_key(client):
    r = client.post("/chat", json={"content": "hola"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["response"] == "Hola mundo"
    r2 = client.post("/chat", json={"content": "hola"}, headers={"X-API-Key": KEY})
    assert r2.status_code == 200


def test_chat_conserva_el_historial_por_sesion(client, seen):
    client.post("/chat", json={"content": "primero", "session_id": "s1"}, headers=AUTH)
    client.post("/chat", json={"content": "segundo", "session_id": "s1"}, headers=AUTH)
    contenidos = [m["content"] for m in seen[1] if m["role"] != "system"]
    assert contenidos == ["primero", "Hola mundo", "segundo"]
    assert seen[1][0]["role"] == "system" and "Fecha y hora actuales" in seen[1][0]["content"]


def test_chat_valida_entrada(client):
    assert client.post("/chat", json={"content": ""}, headers=AUTH).status_code == 422
    assert client.post("/chat", json={"content": "x" * 20001}, headers=AUTH).status_code == 422
    assert client.post("/chat", json={"content": "x", "session_id": "../mal"}, headers=AUTH).status_code == 422
    assert client.post("/chat", json={"content": "x", "image_b64": "###"}, headers=AUTH).status_code == 400


def test_chat_imagen_demasiado_grande(make_settings, monkeypatch):
    async def runner(settings, ctx, messages):
        yield ("token", "ok")

    app = create_app(make_settings(max_image_bytes=1024), runner=runner, store=Store(":memory:"))
    import base64

    big = base64.b64encode(b"a" * 2048).decode()
    with TestClient(app) as c:
        assert c.post("/chat", json={"content": "x", "image_b64": big}, headers=AUTH).status_code == 413


def test_models(client):
    r = client.get("/v1/models", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["data"][0]["id"] == "ia-agent"


def test_completions_sin_stream(client):
    body = {"model": "ia-agent", "messages": [{"role": "user", "content": "hola"}], "stream": False}
    r = client.post("/v1/chat/completions", json=body, headers=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Hola mundo"


def test_completions_stream_sse(client):
    body = {"model": "ia-agent", "messages": [{"role": "user", "content": "hola"}], "stream": True}
    with client.stream("POST", "/v1/chat/completions", json=body, headers=AUTH) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = [ln for ln in r.iter_lines() if ln.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    chunks = [json.loads(ln[6:]) for ln in lines[:-1]]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    texto = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert "*Usando prueba…*" in texto and texto.endswith("Hola mundo")


def test_completions_sin_mensaje_de_usuario(client):
    body = {"messages": [{"role": "system", "content": "x"}]}
    assert client.post("/v1/chat/completions", json=body, headers=AUTH).status_code == 400


def test_completions_convierte_system_e_historial(client, seen):
    body = {
        "messages": [
            {"role": "system", "content": "Responde corto"},
            {"role": "user", "content": "uno"},
            {"role": "assistant", "content": "dos"},
            {"role": "user", "content": [{"type": "text", "text": "tres"}]},
        ]
    }
    client.post("/v1/chat/completions", json=body, headers=AUTH)
    msgs = seen[-1]
    assert "Responde corto" in msgs[0]["content"]
    assert [m["content"] for m in msgs[1:]] == ["uno", "dos", "tres"]


def test_imagen_remota_se_ignora(client, seen):
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "mira"},
                    {"type": "image_url", "image_url": {"url": "http://127.0.0.1/x.png"}},
                ],
            }
        ]
    }
    assert client.post("/v1/chat/completions", json=body, headers=AUTH).status_code == 200
    assert "Imagen remota ignorada" in seen[-1][-1]["content"]


def test_imagen_data_url_invalida(client):
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,@@@"}}]}
        ]
    }
    assert client.post("/v1/chat/completions", json=body, headers=AUTH).status_code == 400


def test_screenshot_no_existe_si_esta_desactivado(client):
    assert client.post("/screenshot", headers=AUTH).status_code == 404


def test_search(client, monkeypatch):
    from agent.tools import web

    monkeypatch.setattr(web, "_search_text", lambda q, n, tr: [{"title": "t", "url": "https://a.example", "snippet": "s"}])
    r = client.get("/search", params={"q": "hola"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["results"][0]["url"] == "https://a.example"
    assert client.get("/search", headers=AUTH).status_code == 422


def test_audio_chat_valida_sesion_y_tamano(client):
    files = {"file": ("a.wav", b"RIFF0000", "audio/wav")}
    r = client.post("/audio-chat", files=files, data={"session_id": "../x"}, headers=AUTH)
    assert r.status_code == 422
    r = client.post("/audio-chat", files={"file": ("a.wav", b"", "audio/wav")}, headers=AUTH)
    assert r.status_code == 400
