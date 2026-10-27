from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass


@dataclass
class ProcResult:
    returncode: int | None
    output: str
    timed_out: bool
    truncated: bool


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def run_limited(
    argv: list[str],
    *,
    input_bytes: bytes | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 10.0,
    max_out: int = 20000,
) -> ProcResult:
    """Ejecuta sin shell, con timeout y salida (stdout+stderr) limitada."""
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    buf = bytearray()
    state = {"truncated": False}

    def reader() -> None:
        stream = proc.stdout
        if stream is None:
            return
        while True:
            chunk = stream.read1(4096)
            if not chunk:
                break
            if len(buf) + len(chunk) > max_out:
                buf.extend(chunk[: max_out - len(buf)])
                state["truncated"] = True
                _kill(proc)
                break
            buf.extend(chunk)

    def writer() -> None:
        try:
            if proc.stdin is not None and input_bytes is not None:
                proc.stdin.write(input_bytes)
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except OSError:
                pass

    t_read = threading.Thread(target=reader, daemon=True)
    t_read.start()
    if input_bytes is not None:
        threading.Thread(target=writer, daemon=True).start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill(proc)
        proc.wait()
    t_read.join(timeout=5)
    return ProcResult(proc.returncode, buf.decode("utf-8", "replace"), timed_out, state["truncated"])
