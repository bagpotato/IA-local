from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from agent.security.fs import PathNotAllowed, ensure_root, resolve_in_workspace
from agent.utils.proc import run_limited


class CommandNotAllowed(Exception):
    pass


ALLOWED = frozenset(
    {"ls", "pwd", "cat", "head", "tail", "wc", "grep", "uniq", "date", "df", "du", "uname", "stat", "file"}
)
FORBIDDEN_CHARS = set(";|&<>$`(){}\\\n\r\x00")
SAFE_PATH = "/usr/local/bin:/usr/bin:/bin"

# Opciones peligrosas por comando (cortas y largas)
BLOCKED_LONG = {
    "grep": {"--dereference-recursive"},
    "date": {"--set"},
    "tail": {"--follow"},
    "du": {"--dereference"},
    "ls": {"--dereference"},
    "stat": {"--dereference"},
    "file": {"--dereference"},
}
BLOCKED_SHORT = {
    "grep": set("R"),
    "date": set("s"),
    "tail": set("fF"),
    "du": set("L"),
    "ls": set("L"),
    "stat": set("L"),
    "file": set("L"),
}


def _pathlike(value: str) -> bool:
    return "/" in value or value == ".." or value.startswith("~")


def _is_symlink(root: Path, value: str) -> bool:
    try:
        return (root / value).is_symlink()
    except (OSError, ValueError):
        return False


def _check_path(value: str, root: Path) -> None:
    if _pathlike(value) or _is_symlink(root, value):
        try:
            resolve_in_workspace(value, root)
        except PathNotAllowed as exc:
            raise CommandNotAllowed(f"Argumento fuera del workspace: {value}") from exc


def _check_option(name: str, tok: str, root: Path) -> None:
    head, sep, value = tok.partition("=")
    if "/" in head:
        raise CommandNotAllowed("Opción con ruta no permitida")
    if tok.startswith("--"):
        if head in BLOCKED_LONG.get(name, set()):
            raise CommandNotAllowed(f"Opción no permitida: {head}")
        if sep:
            _check_path(value, root)
        return
    letters = tok[1:].split("=", 1)[0]
    if set(letters) & BLOCKED_SHORT.get(name, set()):
        raise CommandNotAllowed(f"Opción no permitida: {tok}")
    if sep:
        _check_path(value, root)
    elif len(tok) > 2:
        _check_path(tok[2:], root)


def validate_command(command: str, root: Path | str) -> list[str]:
    if not isinstance(command, str) or not command.strip():
        raise CommandNotAllowed("Comando vacío")
    if len(command) > 500:
        raise CommandNotAllowed("Comando demasiado largo")
    bad = sorted(c for c in set(command) if c in FORBIDDEN_CHARS)
    if bad:
        raise CommandNotAllowed("Carácter no permitido en el comando")
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise CommandNotAllowed("Comando mal formado") from exc
    if not tokens:
        raise CommandNotAllowed("Comando vacío")
    name = tokens[0]
    if name not in ALLOWED:
        raise CommandNotAllowed(f"Comando no permitido: {name}")
    root_r = ensure_root(root)
    for tok in tokens[1:]:
        if tok.startswith("-") and tok != "-":
            _check_option(name, tok, root_r)
        else:
            _check_path(tok, root_r)
    return tokens


def run_command_safe(command: str, root: Path | str, timeout: float = 10.0, max_out: int = 20000) -> str:
    argv = validate_command(command, root)
    exe = shutil.which(argv[0], path=SAFE_PATH)
    if exe is None:
        raise CommandNotAllowed(f"No está instalado: {argv[0]}")
    argv[0] = exe
    root_r = ensure_root(root)
    env = {"PATH": SAFE_PATH, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "HOME": str(root_r)}
    res = run_limited(argv, cwd=str(root_r), env=env, timeout=timeout, max_out=max_out)
    out = res.output
    if res.truncated:
        out += "\n[salida truncada]"
    if res.timed_out:
        out += "\n[tiempo agotado]"
    elif res.returncode not in (0, None):
        out += f"\n[código de salida {res.returncode}]"
    return out.strip() or "(sin salida)"
