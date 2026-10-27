from __future__ import annotations

import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx


class FetchError(Exception):
    pass


class BlockedURL(FetchError):
    pass


ALLOWED_SCHEMES = {"http", "https"}
TEXT_TYPES = (
    "text/",
    "application/xhtml+xml",
    "application/xml",
    "application/json",
    "application/rss+xml",
    "application/atom+xml",
)
REDIRECT_CODES = {301, 302, 303, 307, 308}
CGNAT = ipaddress.ip_network("100.64.0.0/10")
NAT64 = ipaddress.ip_network("64:ff9b::/96")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

Resolver = Callable[[str, int], list[str]]


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str
    text: str
    truncated: bool = False


def _default_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"No se pudo resolver {host}") from exc
    return sorted({info[4][0] for info in infos})


def is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public_ip(str(ip.ipv4_mapped))
        if ip.sixtofour is not None:
            return is_public_ip(str(ip.sixtofour))
        if ip.teredo is not None:
            server, client = ip.teredo
            return is_public_ip(str(server)) and is_public_ip(str(client))
        if ip in NAT64:
            return is_public_ip(str(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)))
    elif ip in CGNAT:
        return False
    return ip.is_global and not ip.is_multicast


def validate_url(url: str, resolver: Resolver | None = None) -> tuple[str, list[str]]:
    """Valida esquema, credenciales y que todas las IP resueltas sean públicas."""
    if not isinstance(url, str) or not url.strip():
        raise BlockedURL("URL vacía")
    url = url.strip()
    if len(url) > 2048 or re.search(r"[\x00-\x20]", url):
        raise BlockedURL("URL no válida")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise BlockedURL("URL no válida") from exc
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise BlockedURL("Solo se permiten URLs http/https")
    if parts.username or parts.password or "@" in parts.netloc:
        raise BlockedURL("No se permiten credenciales en la URL")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise BlockedURL("URL sin host")
    port = port or (443 if parts.scheme.lower() == "https" else 80)
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        ips = (resolver or _default_resolver)(host, port)
    if not ips:
        raise BlockedURL(f"Sin direcciones para {host}")
    for ip in ips:
        if not is_public_ip(ip):
            raise BlockedURL(f"Destino no público bloqueado ({host})")
    return url, ips


def _decode(raw: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type, re.I)
    if not m:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4096], re.I)
        charset = m.group(1).decode("ascii", "ignore") if m else ""
    else:
        charset = m.group(1)
    if charset:
        try:
            return raw.decode(charset, errors="replace")
        except LookupError:
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def safe_get(
    url: str,
    *,
    client: httpx.Client | None = None,
    resolver: Resolver | None = None,
    max_redirects: int = 5,
    max_bytes: int = 2 * 1024 * 1024,
    timeout: float = 20.0,
) -> FetchResult:
    """GET con validación SSRF en cada salto, límite de tamaño y de tiempo total."""
    deadline = time.monotonic() + timeout
    own = client is None
    if client is None:
        client = httpx.Client(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
                "Accept-Language": "es,en;q=0.8",
            },
        )
    try:
        current = url
        for _ in range(max_redirects + 1):
            current, _ips = validate_url(current, resolver)
            if time.monotonic() > deadline:
                raise FetchError("Tiempo de espera agotado")
            with client.stream("GET", current) as resp:
                if resp.status_code in REDIRECT_CODES:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError("Redirección sin Location")
                    current = urljoin(current, location)
                    continue
                header_ct = resp.headers.get("content-type", "")
                ctype = header_ct.split(";")[0].strip().lower()
                if ctype and not ctype.startswith(TEXT_TYPES):
                    raise FetchError(f"Tipo de contenido no permitido: {ctype}")
                if resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code}")
                buf = bytearray()
                truncated = False
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        del buf[max_bytes:]
                        truncated = True
                        break
                    if time.monotonic() > deadline:
                        raise FetchError("Tiempo de espera agotado")
                return FetchResult(current, resp.status_code, ctype, _decode(bytes(buf), header_ct), truncated)
        raise FetchError("Demasiadas redirecciones")
    except httpx.HTTPError as exc:
        raise FetchError(f"Error de red: {exc.__class__.__name__}") from exc
    finally:
        if own:
            client.close()
