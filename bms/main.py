"""Launch BoreyBMS.

Run ``python main.py`` for development. ``build_windows_exe.bat`` builds
``dist/BoreyBMS-Setup.exe`` for Windows. The installer bundles the app and
its Python dependencies; Inno Setup 6 is required only on the build machine.
Application data lives beside the installed program.
"""

from __future__ import annotations

import ctypes
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

_INSTANCE_MUTEX = None


def main() -> None:
    mode = os.environ.get("BMS_MODE", "offline").strip().lower()
    if mode not in {"offline", "online"}:
        raise RuntimeError("BMS_MODE must be either 'offline' or 'online'.")
    if mode == "online":
        if not os.environ.get("BMS_DB"):
            raise RuntimeError("Set BMS_DB to a persistent database path in online mode.")
        session_secret = os.environ.get("BMS_SESSION_SECRET", "")
        if len(session_secret) < 32:
            raise RuntimeError("Set BMS_SESSION_SECRET to a random value of at least 32 characters.")
    host = os.environ.get("BMS_HOST", "0.0.0.0" if mode == "online" else "127.0.0.1")
    port = int(os.environ.get("BMS_PORT", "8080"))
    open_browser = mode == "offline" and os.environ.get("BMS_OPEN_BROWSER", "1") == "1" and host in (
        "127.0.0.1",
        "localhost",
    )
    if open_browser and _wait_for_health(port, timeout=0.5):
        _open(port)
        return
    if mode == "offline" and not _acquire_instance_mutex(port):
        if open_browser:
            if _wait_for_health(port, timeout=15):
                _open(port)
            else:
                _show_error("BoreyBMS is already running but did not respond.")
        return
    if not _port_available(port):
        _show_error(
            f"Port {port} is already in use. Close the other app using this port, "
            "then open BoreyBMS again."
        )
        return
    from app import app

    if open_browser:
        threading.Thread(target=lambda: _open_when_ready(port), daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="info")


def _acquire_instance_mutex(port: int) -> bool:
    global _INSTANCE_MUTEX
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.CreateMutexW(None, True, f"Local\\MeridianBMS-Server-{port}")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(handle)
        return False
    _INSTANCE_MUTEX = handle
    return True


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _wait_for_health(port: int, timeout: float) -> bool:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def _open_when_ready(port: int) -> None:
    if _wait_for_health(port, timeout=15):
        _open(port)
    else:
        _show_error("BoreyBMS started but did not become ready.")


def _open(port: int) -> None:
    url = f"http://127.0.0.1:{port}/"
    if not webbrowser.open(url):
        _show_error(f"BoreyBMS is running. Open this address in your browser:\n{url}")


def _show_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "BoreyBMS", 0x10)


if __name__ == "__main__":
    main()
