"""Start quota-hub in one quiet process and open the local dashboard."""
from __future__ import annotations

import ctypes
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from app.main import app

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = int(os.environ.get("QUOTA_HUB_PORT", "8788"))
URL = f"http://{HOST}:{PORT}/"
HEALTH = f"http://{HOST}:{PORT}/api/health"
LOG_PATH = ROOT / "data" / "quota-hub.log"
LOGGER = logging.getLogger("quota-hub.launch")


def _configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=1_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler],
        force=True,
    )


def _port_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((HOST, PORT)) == 0


def _is_ours() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=1.5) as resp:
            body = resp.read(256).decode("utf-8", "ignore")
        if '"quota-hub"' in body:
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    try:
        with urllib.request.urlopen(URL, timeout=1.5) as resp:
            body = resp.read(512).decode("utf-8", "ignore")
        return "额度看板" in body
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _notify(title: str, message: str) -> None:
    """Report startup failures without opening a console window."""
    LOGGER.error("%s: %s", title, message)
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
            return
        except Exception:
            pass
    print(f"{title}: {message}")


def _open_when_ready(server: uvicorn.Server | None = None) -> None:
    for _ in range(60):
        if _port_open() and _is_ours():
            try:
                if os.environ.get("QUOTA_HUB_NO_BROWSER") not in {"1", "true", "yes"}:
                    webbrowser.open(URL)
            except Exception as exc:
                LOGGER.warning("无法打开浏览器: %s", exc)
            return
        time.sleep(0.25)
    _notify("额度看板", f"服务启动超时，请查看日志：{LOG_PATH}")
    if server is not None:
        server.should_exit = True


def main() -> int:
    _configure_logging()
    os.chdir(ROOT)

    if _port_open():
        if _is_ours():
            if os.environ.get("QUOTA_HUB_NO_BROWSER") not in {"1", "true", "yes"}:
                webbrowser.open(URL)
            return 0
        _notify("额度看板", f"端口 {PORT} 已被其它程序占用。")
        return 1

    try:
        config = uvicorn.Config(
            app,
            host=HOST,
            port=PORT,
            log_level="warning",
            access_log=False,
            log_config=None,
        )
        server = uvicorn.Server(config)
        app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
        threading.Thread(
            target=_open_when_ready,
            args=(server,),
            name="quota-hub-browser",
            daemon=True,
        ).start()
        server.run()
        return 0
    except Exception as exc:
        _notify("额度看板启动失败", f"{exc}\n\n日志：{LOG_PATH}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
