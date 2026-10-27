import os
import shutil
import tempfile

import httpx
import pytest

from agent.security import commands, fs, netguard

BLOCKED_URLS = [
    "http://127.0.0.1/",
    "http://127.0.0.1:8000/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.1/",
    "http://100.100.100.100/",
    "http://[::ffff:127.0.0.1]/",
    "http://[::1]/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://0.0.0.0/",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "http://user:clave@example.com/",
]


def _can_symlink():
    with tempfile.TemporaryDirectory() as d:
        try:
            os.symlink(d, os.path.join(d, "l"))
            return True
        except (OSError, NotImplementedError):
            return False


needs_symlink = pytest.mark.skipif(not _can_symlink(), reason="symlinks no disponibles")
needs_ls = pytest.mark.skipif(
    shutil.which("ls", path=commands.SAFE_PATH) is None, reason="ls no disponible en SAFE_PATH"
)


def _no_dns(host, port):
    raise AssertionError("no debería resolver DNS para una IP literal")


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_ssrf_bloquea_destinos_no_publicos(url):
    with pytest.raises(netguard.BlockedURL):
        netguard.validate_url(url, resolver=_no_dns)


def test_ssrf_permite_ip_publica():
    url, ips = netguard.validate_url("https://example.com/a", resolver=lambda h, p: ["93.184.216.34"])
    assert url == "https://example.com/a"
    assert ips == ["93.184.216.34"]


def test_ssrf_dns_a_ip_privada_se_bloquea():
    with pytest.raises(netguard.BlockedURL):
        netguard.validate_url("https://interno.example/", resolver=lambda h, p: ["127.0.0.1"])


def test_ssrf_una_ip_privada_entre_varias_se_bloquea():
    with pytest.raises(netguard.BlockedURL):
        netguard.validate_url("https://mixto.example/", resolver=lambda h, p: ["93.184.216.34", "10.0.0.1"])


def test_is_public_ip():
    assert netguard.is_public_ip("8.8.8.8")
    assert netguard.is_public_ip("::ffff:8.8.8.8")
    assert not netguard.is_public_ip("::ffff:127.0.0.1")
    assert not netguard.is_public_ip("100.64.0.1")
    assert not netguard.is_public_ip("no-es-ip")


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


PUBLIC = lambda host, port: ["93.184.216.34"]  # noqa: E731


def test_redireccion_a_ip_interna_se_bloquea():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1:11434/api/tags"})

    with pytest.raises(netguard.BlockedURL):
        netguard.safe_get("https://example.com/", client=_client(handler), resolver=PUBLIC)


def test_redireccion_valida_se_sigue():
    def handler(request):
        if request.url.path == "/a":
            return httpx.Response(301, headers={"location": "/b"})
        return httpx.Response(200, headers={"content-type": "text/plain; charset=utf-8"}, content=b"hola")

    res = netguard.safe_get("https://example.com/a", client=_client(handler), resolver=PUBLIC)
    assert res.text == "hola"
    assert res.url == "https://example.com/b"


def test_demasiadas_redirecciones():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://example.com/"})

    with pytest.raises(netguard.FetchError):
        netguard.safe_get("https://example.com/", client=_client(handler), resolver=PUBLIC, max_redirects=5)


def test_tipo_de_contenido_no_textual_se_rechaza():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG")

    with pytest.raises(netguard.FetchError):
        netguard.safe_get("https://example.com/x.png", client=_client(handler), resolver=PUBLIC)


def test_limite_de_tamano():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"a" * 5000)

    res = netguard.safe_get("https://example.com/", client=_client(handler), resolver=PUBLIC, max_bytes=1000)
    assert res.truncated
    assert len(res.text) == 1000


# --- workspace ---

@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (tmp_path / "fuera").mkdir()
    (tmp_path / "fuera" / "secreto.txt").write_text("secreto")
    return root


def test_ruta_dentro_del_workspace(ws):
    p = fs.resolve_in_workspace("sub/a.txt", ws)
    assert p == (ws / "sub" / "a.txt").resolve()


@pytest.mark.parametrize("bad", ["../fuera/secreto.txt", "/etc/passwd", "a/../../fuera", "~/x", "a\x00b"])
def test_ruta_fuera_del_workspace(ws, bad):
    with pytest.raises(fs.PathNotAllowed):
        fs.resolve_in_workspace(bad, ws)


@needs_symlink
def test_symlink_que_escapa(ws, tmp_path):
    (ws / "enlace").symlink_to(tmp_path / "fuera")
    with pytest.raises(fs.PathNotAllowed):
        fs.resolve_in_workspace("enlace/secreto.txt", ws)
    with pytest.raises(fs.PathNotAllowed):
        fs.read_text("enlace/secreto.txt", ws, 1000, 1000)


def test_escritura_y_lectura(ws):
    fs.write_text("notas/a.txt", "hola", ws, 1000)
    assert fs.read_text("notas/a.txt", ws, 1000, 1000) == "hola"
    fs.write_text("notas/a.txt", " mundo", ws, 1000, append=True)
    assert fs.read_text("notas/a.txt", ws, 1000, 1000) == "hola mundo"


def test_escritura_extension_y_tamano(ws):
    with pytest.raises(fs.PathNotAllowed):
        fs.write_text("x.sh", "echo", ws, 1000)
    with pytest.raises(fs.PathNotAllowed):
        fs.write_text("x.txt", "a" * 2000, ws, 1000)


# --- comandos ---

BAD_COMMANDS = [
    "ls; rm -rf /",
    "ls && cat /etc/passwd",
    "ls | wc -l",
    "cat a > b",
    "ls $(whoami)",
    "ls `id`",
    "cat --file=/etc/passwd",
    "grep --file=/etc/passwd x",
    "grep -f/etc/passwd x",
    "cat /etc/passwd",
    "cat ../fuera/secreto.txt",
    "ls ..",
    "ls ~",
    "ls ../",
    "/bin/ls",
    "python -c 1",
    "pip list",
    "git status",
    "docker ps",
    "curl http://example.com",
    "rm x",
    "kill 1",
    "sort x",
    "find .",
    "tail -f x",
    "date -s 2020-01-01",
    "du -L .",
    "",
]


@pytest.mark.parametrize("cmd", BAD_COMMANDS)
def test_comandos_rechazados(ws, cmd):
    with pytest.raises(commands.CommandNotAllowed):
        commands.validate_command(cmd, ws)


@needs_symlink
def test_comando_con_symlink_rechazado(ws, tmp_path):
    (ws / "enlace").symlink_to(tmp_path / "fuera" / "secreto.txt")
    with pytest.raises(commands.CommandNotAllowed):
        commands.validate_command("cat enlace", ws)
    with pytest.raises(commands.CommandNotAllowed):
        commands.validate_command("cat --x=enlace", ws)


@pytest.mark.parametrize("cmd", ["ls -la", "pwd", "wc -l notas.txt", "head -n 5 notas.txt", "grep -n hola notas.txt"])
def test_comandos_permitidos(ws, cmd):
    assert commands.validate_command(cmd, ws)[0] in commands.ALLOWED


@needs_ls
def test_ejecucion_real_de_ls(ws):
    (ws / "notas.txt").write_text("hola\nmundo\n")
    out = commands.run_command_safe("ls", ws)
    assert "notas.txt" in out
    assert "hola" in commands.run_command_safe("grep hola notas.txt", ws)
