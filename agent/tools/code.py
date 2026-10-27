from __future__ import annotations

import os
import shutil
import subprocess
import uuid

from pydantic import BaseModel, Field

from agent.config import Settings
from agent.registry import Ctx, ToolSpec
from agent.utils.proc import run_limited


class RunPythonArgs(BaseModel):
    code: str = Field(..., min_length=1, max_length=20000, description="Código Python 3 a ejecutar")


def _docker_env() -> dict[str, str]:
    keep = ("HOME", "DOCKER_HOST", "DOCKER_CONFIG", "XDG_RUNTIME_DIR")
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}
    env.update({k: os.environ[k] for k in keep if k in os.environ})
    return env


def run_python_sandbox(code: str, settings: Settings) -> str:
    """Ejecuta Python en un contenedor efímero sin red, sin montajes y con límites de recursos."""
    docker = shutil.which("docker")
    if not docker:
        return "ERROR: docker no está disponible en el host."
    name = f"ia-sandbox-{uuid.uuid4().hex[:12]}"
    cmd = [
        docker, "run", "--rm", "-i", "--name", name,
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,size=32m",
        "--memory", "256m", "--memory-swap", "256m",
        "--cpus", "0.5",
        "--pids-limit", "64",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "65534:65534",
        settings.sandbox_image,
        "python", "-I", "-",
    ]
    try:
        res = run_limited(
            cmd, input_bytes=code.encode("utf-8"), env=_docker_env(),
            timeout=settings.code_timeout, max_out=20000,
        )
    finally:
        try:
            subprocess.run(
                [docker, "rm", "-f", name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False, env=_docker_env(),
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
    out = res.output.strip()
    if res.truncated:
        out += "\n[salida truncada]"
    if res.timed_out:
        return (out + "\n[tiempo agotado]").strip()
    return f"[código de salida {res.returncode}]\n{out}".strip()


def _run_python(ctx: Ctx, a: RunPythonArgs) -> str:
    return run_python_sandbox(a.code, ctx.settings)


SPECS = [
    ToolSpec(
        "run_python",
        "Ejecuta código Python 3 en un sandbox aislado (sin red ni ficheros). Imprime con print() lo que quieras ver.",
        RunPythonArgs, _run_python, "code", "executor", dangerous=True,
    ),
]
