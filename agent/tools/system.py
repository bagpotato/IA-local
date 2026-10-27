from __future__ import annotations

from pydantic import BaseModel, Field

from agent.registry import Ctx, ToolSpec
from agent.security import commands, fs


class ListFilesArgs(BaseModel):
    path: str = Field(".", max_length=500, description="Directorio dentro del workspace")


class ReadFileArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=500, description="Fichero dentro del workspace")
    max_chars: int = Field(20000, ge=100, le=100000, description="Máximo de caracteres")


class WriteFileArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=500, description="Fichero destino dentro del workspace")
    content: str = Field(..., max_length=200000, description="Contenido de texto")
    append: bool = Field(False, description="Añadir al final en lugar de sobrescribir")


class RunCommandArgs(BaseModel):
    command: str = Field(..., min_length=1, max_length=500, description="Comando de la lista permitida")


def _list_files(ctx: Ctx, a: ListFilesArgs) -> str:
    try:
        lines = fs.list_dir(a.path, ctx.settings.workspace_dir)
    except (fs.PathNotAllowed, OSError) as exc:
        return f"ERROR: {exc}"
    return "\n".join(lines) if lines else "(directorio vacío)"


def _read_file(ctx: Ctx, a: ReadFileArgs) -> str:
    try:
        return fs.read_text(a.path, ctx.settings.workspace_dir, a.max_chars, ctx.settings.max_file_bytes)
    except (fs.PathNotAllowed, OSError) as exc:
        return f"ERROR: {exc}"


def _write_file(ctx: Ctx, a: WriteFileArgs) -> str:
    try:
        target, size = fs.write_text(
            a.path, a.content, ctx.settings.workspace_dir, ctx.settings.max_file_bytes, append=a.append
        )
    except (fs.PathNotAllowed, OSError) as exc:
        return f"ERROR: {exc}"
    return f"Escrito {fs.relative_name(target, ctx.settings.workspace_dir)} ({size} bytes)"


def _run_command(ctx: Ctx, a: RunCommandArgs) -> str:
    try:
        return commands.run_command_safe(a.command, ctx.settings.workspace_dir)
    except (commands.CommandNotAllowed, fs.PathNotAllowed, OSError) as exc:
        return f"ERROR: {exc}"


SPECS = [
    ToolSpec("list_files", "Lista los ficheros de un directorio del workspace.", ListFilesArgs, _list_files, "files"),
    ToolSpec("read_file", "Lee un fichero de texto del workspace.", ReadFileArgs, _read_file, "files"),
    ToolSpec(
        "write_file",
        "Escribe un fichero de texto en el workspace (extensiones de texto permitidas).",
        WriteFileArgs, _write_file, "files", dangerous=True,
    ),
    ToolSpec(
        "run_command",
        "Ejecuta un comando de solo lectura del workspace (ls, pwd, cat, head, tail, wc, grep, uniq, date, df, "
        "du, uname, stat, file). Sin pipes, redirecciones ni rutas fuera del workspace.",
        RunCommandArgs, _run_command, "files", dangerous=True,
    ),
]
