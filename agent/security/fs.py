from __future__ import annotations

from pathlib import Path


class PathNotAllowed(Exception):
    pass


ALLOWED_WRITE_EXT = {
    ".txt", ".md", ".py", ".json", ".csv", ".tsv", ".html", ".htm", ".css", ".js",
    ".yml", ".yaml", ".toml", ".ini", ".xml", ".log",
}


def ensure_root(root: Path | str) -> Path:
    root = Path(root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def resolve_in_workspace(path: str, root: Path | str) -> Path:
    """Resuelve (siguiendo symlinks) y exige que el destino quede dentro del workspace."""
    if not isinstance(path, str) or "\x00" in path:
        raise PathNotAllowed("Ruta no válida")
    raw = path.strip() or "."
    if raw.startswith("~"):
        raise PathNotAllowed("No se permiten rutas con ~")
    root_r = ensure_root(root)
    p = Path(raw)
    candidate = p if p.is_absolute() else root_r / p
    resolved = candidate.resolve()
    if resolved != root_r and not resolved.is_relative_to(root_r):
        raise PathNotAllowed("Ruta fuera del workspace")
    return resolved


def relative_name(path: Path, root: Path | str) -> str:
    try:
        return str(path.relative_to(ensure_root(root))) or "."
    except ValueError:
        return str(path)


def list_dir(path: str, root: Path | str, limit: int = 200) -> list[str]:
    target = resolve_in_workspace(path, root)
    if not target.is_dir():
        raise PathNotAllowed("No es un directorio")
    lines: list[str] = []
    for entry in sorted(target.iterdir(), key=lambda e: e.name.lower())[:limit]:
        if entry.is_symlink():
            lines.append(f"l {entry.name}")
        elif entry.is_dir():
            lines.append(f"d {entry.name}/")
        else:
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            lines.append(f"f {entry.name} ({size} bytes)")
    return lines


def read_text(path: str, root: Path | str, max_chars: int, max_bytes: int) -> str:
    target = resolve_in_workspace(path, root)
    if not target.is_file():
        raise PathNotAllowed("No es un fichero")
    with open(target, "rb") as fh:
        data = fh.read(max_bytes + 1)
    if b"\x00" in data[:8000]:
        raise PathNotAllowed("Fichero binario")
    text = data[:max_bytes].decode("utf-8", errors="replace")
    if len(data) > max_bytes or len(text) > max_chars:
        text = text[:max_chars] + "\n[... recortado]"
    return text


def write_text(path: str, content: str, root: Path | str, max_bytes: int, append: bool = False) -> tuple[Path, int]:
    target = resolve_in_workspace(path, root)
    if target.suffix.lower() not in ALLOWED_WRITE_EXT:
        raise PathNotAllowed(f"Extensión no permitida: {target.suffix or '(sin extensión)'}")
    if target == ensure_root(root) or target.is_dir():
        raise PathNotAllowed("El destino es un directorio")
    data = content.encode("utf-8")
    existing = target.stat().st_size if (append and target.exists()) else 0
    if len(data) + existing > max_bytes:
        raise PathNotAllowed(f"Supera el límite de {max_bytes} bytes")
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "ab" if append else "wb") as fh:
        fh.write(data)
    return target, len(data)
