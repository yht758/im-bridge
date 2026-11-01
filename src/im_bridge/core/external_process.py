"""Launch an independent runtime without the console's Python/Qt environment."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

_launch_lock = threading.Lock()


def clean_environment() -> dict[str, str]:
    env = dict(os.environ)
    for name in list(env):
        if name in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"} or name.startswith("_PYI_"):
            env.pop(name, None)
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    frozen_root = str(getattr(sys, "_MEIPASS", ""))
    if frozen_root:
        env["PATH"] = os.pathsep.join(part for part in env.get("PATH", "").split(os.pathsep)
                                       if not part.lower().startswith(frozen_root.lower()))
        for key in ("LD_LIBRARY_PATH", "LIBPATH"):
            if key in env:
                env[key] = env.get(key + "_ORIG", "")
    return env


def start_external(command: list[str], *, cwd: str, **overrides):
    options = dict(cwd=cwd, env=clean_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   text=True, encoding="utf-8", errors="replace")
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    options.update(overrides)
    with _launch_lock:
        kernel = None
        previous = None
        if os.name == "nt" and getattr(sys, "frozen", False):
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
            length = kernel.GetDllDirectoryW(0, None)
            buffer = ctypes.create_unicode_buffer(length + 1)
            kernel.GetDllDirectoryW(len(buffer), buffer)
            previous = buffer.value
            kernel.SetDllDirectoryW(None)
        try:
            process = subprocess.Popen(command, **options)
        finally:
            if kernel is not None:
                kernel.SetDllDirectoryW(previous or None)
    return process


def run_external(command: list[str], *, cwd: str, timeout: float = 300):
    process = start_external(command, cwd=cwd)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Killing only convert.py leaves its inference child running on the GPU.
        if os.name == "nt":
            taskkill = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe"
            subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.kill()
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
