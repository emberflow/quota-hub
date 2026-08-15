"""Start the local dashboard and open the browser."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8787
URL = f"http://{HOST}:{PORT}/"
HEALTH = f"http://{HOST}:{PORT}/api/health"


def _port_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((HOST, PORT)) == 0


def _is_ours() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=1.5) as resp:
            body = resp.read().decode("utf-8", "ignore")
        if '"quota-hub"' in body:
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    try:
        with urllib.request.urlopen(URL, timeout=1.5) as resp:
            body = resp.read().decode("utf-8", "ignore")
        return "额度看板" in body
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _python() -> str:
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv.exists():
        return str(venv)
    return sys.executable


def main() -> int:
    if os.name == "nt":
        try:
            os.system("title 额度看板")
        except Exception:
            pass
    os.chdir(ROOT)

    if _port_open():
        if not _is_ours():
            print(f"端口 {PORT} 已被其它程序占用，无法启动看板。")
            input("按回车退出…")
            return 1
        webbrowser.open(URL)
        print(f"看板已在运行：{URL}")
        return 0

    proc = subprocess.Popen(
        [
            _python(),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            HOST,
            "--port",
            str(PORT),
        ],
        cwd=str(ROOT),
    )
    for _ in range(60):
        if proc.poll() is not None:
            print("服务启动失败。")
            return proc.returncode or 1
        if _port_open():
            break
        time.sleep(0.25)
    else:
        print("服务启动超时。")
        proc.terminate()
        return 1

    webbrowser.open(URL)
    print(f"已打开 {URL}")
    print("关闭本窗口会停止看板。")
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
