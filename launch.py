# -*- coding: utf-8 -*-
"""Open the Streamlit app: kill stale server on PORT, then start fresh."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

PORT = 8501
ROOT = Path(__file__).resolve().parent
URL = f"http://127.0.0.1:{PORT}"


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _win_flags() -> int:
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def kill_port(port: int) -> None:
    """Stop whatever is listening on PORT (stale Streamlit = blank page)."""
    if sys.platform != "win32":
        return
    flags = _win_flags()
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=flags,
        )
    except Exception:
        return
    pids: set[int] = set()
    needle = f":{port} "
    for line in out.splitlines():
        if needle not in line or "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pids.add(int(parts[-1]))
        except ValueError:
            continue
    for pid in pids:
        if pid <= 0:
            continue
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                creationflags=flags,
            )
        except Exception:
            pass
    # wait until port is free (skip TIME_WAIT by trying bind)
    for _ in range(40):
        if not port_open(port):
            return
        time.sleep(0.15)


def main() -> int:
    # Always restart: old process keeps broken imports / blank UI after path moves.
    force = "--reuse" not in sys.argv
    if force and port_open(PORT):
        kill_port(PORT)
        time.sleep(0.4)

    if port_open(PORT):
        webbrowser.open(URL)
        print(f"already running → {URL}")
        return 0

    flags = _win_flags()
    if sys.platform == "win32":
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0x00000008)

    log = ROOT / "data" / "streamlit.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    # truncate log so blank-page debug is fresh
    log_f = open(log, "w", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    subprocess.Popen(
        [
            sys.executable.replace("pythonw.exe", "python.exe")
            if sys.executable.lower().endswith("pythonw.exe")
            else sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / "app.py"),
            "--server.headless=true",
            f"--server.port={PORT}",
            "--browser.gatherUsageStats=false",
        ],
        cwd=str(ROOT),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        close_fds=True,
        env=env,
    )

    for _ in range(80):
        time.sleep(0.25)
        if port_open(PORT):
            webbrowser.open(URL)
            print(f"started → {URL}")
            return 0

    print("Streamlit 启动超时，请看 data/streamlit.log 或运行 run.bat")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
