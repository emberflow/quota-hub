"""Read ChatGPT Plus / Codex remaining quota from local login.

Uses the same first-party usage endpoints as quota-axi, then falls back to
the already-installed Codex CLI app-server. Does not write auth.json.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .models import OFFICIAL, Provider, Window
from .timefmt import annotate_window, parse_ts

USAGE_URLS = (
    "https://chatgpt.com/backend-api/wham/usage",
    "https://chatgpt.com/backend-api/codex/usage",
)
CODEX_BIN = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"


def _auth_path() -> Path:
    home = os.environ.get("CODEX_HOME")
    if home:
        return Path(home) / "auth.json"
    return Path.home() / ".codex" / "auth.json"


def _obj(value: Any) -> Optional[dict]:
    return value if isinstance(value, dict) else None


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _jwt_payload(token: str) -> Optional[dict]:
    import base64

    try:
        part = token.split(".")[1]
        pad = "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part + pad))
    except Exception:
        return None


def _jwt_expired(token: str) -> bool:
    payload = _jwt_payload(token) or {}
    exp = _num(payload.get("exp"))
    if exp is None:
        return False
    return exp <= time.time()


def _read_credentials() -> tuple[Optional[str], Optional[str], str]:
    path = _auth_path()
    if not path.exists():
        return None, None, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, None, "invalid"
    tokens = _obj(data.get("tokens")) or {}
    access = _str(tokens.get("access_token") or tokens.get("accessToken"))
    if not access:
        return None, None, "invalid"
    if _jwt_expired(access):
        return None, None, "expired"
    account_id = _str(tokens.get("account_id") or tokens.get("accountId"))
    return access, account_id, "available"


def _rate_limit_container(data: dict) -> dict:
    return (
        _obj(data.get("rate_limit"))
        or _obj(data.get("rateLimits"))
        or _obj(data.get("rate_limits"))
        or data
    )


def _window_from_raw(raw: Any, window_id: str, label: str, kind: str) -> Optional[Window]:
    item = _obj(raw)
    if not item:
        return None
    used = _num(item.get("used_percent") if "used_percent" in item else item.get("usedPercent"))
    if used is None:
        return None
    used = max(0.0, min(100.0, used))
    left = round(100.0 - used, 2)
    reset_after = _num(item.get("reset_after_seconds"))
    resets = parse_ts(item.get("reset_at") or item.get("resetsAt"))
    if resets is None and reset_after is not None:
        resets = datetime.now(timezone.utc) + timedelta(seconds=reset_after)
    meta = annotate_window(left, resets)
    return Window(
        id=window_id,
        label=label,
        kind=kind,
        percent_remaining=left,
        percent_used=round(used, 2),
        **meta,
    )


def _windows_from_usage(data: dict) -> list[Window]:
    rate = _rate_limit_container(data)
    windows: list[Window] = []
    for raw, wid, label, kind in (
        (rate.get("primary_window") or rate.get("primary"), "five_hour", "5 小时窗口", "session"),
        (rate.get("secondary_window") or rate.get("secondary"), "weekly", "周窗口", "weekly"),
    ):
        win = _window_from_raw(raw, wid, label, kind)
        if win:
            windows.append(win)
    extra = data.get("additional_rate_limits")
    if isinstance(extra, list):
        for entry in extra:
            item = _obj(entry)
            if not item:
                continue
            name = _str(item.get("limit_name") or item.get("metered_feature")) or "extra"
            container = _obj(item.get("rate_limit")) or {}
            for raw, suffix, kind in (
                (container.get("primary_window") or container.get("primary"), "5h", "session"),
                (container.get("secondary_window") or container.get("secondary"), "7d", "weekly"),
            ):
                win = _window_from_raw(raw, f"model:{name}:{suffix}", f"{name} {suffix}", kind)
                if win:
                    windows.append(win)
    return windows


def _credits_extra(data: dict, rate: dict) -> str:
    credits = _obj(data.get("credits")) or _obj(rate.get("credits")) or {}
    remaining = _num(credits.get("balance") if "balance" in credits else credits.get("remaining"))
    if remaining is None:
        return ""
    return f"credits {remaining:.1f}"


def _provider_from_usage(data: dict, source: str) -> Provider:
    p = Provider(
        id="codex",
        label="ChatGPT Plus · Codex",
        official_url=OFFICIAL["codex"],
        source=source,
        status="fresh",
    )
    p.plan = _str(data.get("plan_type") or data.get("planType")) or ""
    rate = _rate_limit_container(data)
    extra = _credits_extra(data, rate)
    p.windows = _windows_from_usage(data)
    if extra and p.windows:
        target = next((w for w in p.windows if w.id == "weekly"), p.windows[0])
        target.extra = extra
    if not p.windows:
        p.status = "unavailable"
        p.error = "没有窗口数据"
        p.remedy = "运行 codex login"
    return p


def _http_usage(token: str, account_id: Optional[str]) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "quota-hub/0.1",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    last_err = "Codex quota unavailable"
    auth_rejected = False
    for url in USAGE_URLS:
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            data = json.loads(raw)
            if _windows_from_usage(data):
                return data
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                auth_rejected = True
                continue
            if exc.code == 429:
                raise RuntimeError("Codex 额度接口限流，稍后再试") from exc
            last_err = f"HTTP {exc.code}"
        except Exception as exc:
            last_err = str(exc)
    if auth_rejected:
        raise RuntimeError("Codex sign-in required")
    raise RuntimeError(last_err)


class _LineRpc:
    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._buf = ""
        self._lock = threading.Lock()
        self._pending: dict[int, dict] = {}
        self._cv = threading.Condition(self._lock)
        self._dead = False
        t = threading.Thread(target=self._read, daemon=True)
        t.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                mid = msg.get("id")
                if not isinstance(mid, int):
                    continue
                with self._cv:
                    self._pending[mid] = msg
                    self._cv.notify_all()
        finally:
            with self._cv:
                self._dead = True
                self._cv.notify_all()

    def call(self, req_id: int, method: str, params: Any = None, timeout: float = 8.0) -> Any:
        payload = {"id": req_id, "method": method, "params": params if params is not None else {}}
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        with self._cv:
            while req_id not in self._pending:
                remaining = deadline - time.time()
                if remaining <= 0 or self._dead:
                    raise TimeoutError(f"Codex RPC timeout: {method}")
                self._cv.wait(timeout=remaining)
            msg = self._pending.pop(req_id)
        if msg.get("error"):
            raise RuntimeError(str(msg["error"]))
        return msg.get("result") if "result" in msg else msg.get("params")


def _codex_exe() -> Optional[str]:
    found = shutil.which("codex")
    if found:
        return found
    if CODEX_BIN.exists():
        return str(CODEX_BIN)
    return None


def _cli_usage() -> dict:
    exe = _codex_exe()
    if not exe:
        raise RuntimeError("未找到 codex CLI")
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [exe, "-s", "read-only", "-a", "untrusted", "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
        creationflags=flags,
    )
    rpc = _LineRpc(proc)
    try:
        rpc.call(1, "initialize", {"clientInfo": {"name": "quota-hub", "version": "1"}}, timeout=15)
        try:
            account = rpc.call(2, "account/read", {}, timeout=8)
        except Exception:
            account = {}
        limits = rpc.call(3, "account/rateLimits/read", {}, timeout=8)
        account_obj = _obj(account) or {}
        account_rec = _obj(account_obj.get("account")) or account_obj
        limit_obj = _obj(limits) or {}
        merged = dict(limit_obj)
        merged.setdefault("email", account_rec.get("email") or limit_obj.get("email"))
        merged.setdefault(
            "account_id",
            account_rec.get("account_id") or account_rec.get("accountId") or limit_obj.get("account_id"),
        )
        merged.setdefault(
            "plan_type",
            account_rec.get("plan_type") or account_rec.get("planType") or limit_obj.get("plan_type"),
        )
        return merged
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()


def collect_codex() -> Provider:
    p = Provider(
        id="codex",
        label="ChatGPT Plus · Codex",
        official_url=OFFICIAL["codex"],
        source="codex local auth",
    )
    errors: list[str] = []
    token, account_id, cred_status = _read_credentials()
    if token:
        try:
            return _provider_from_usage(_http_usage(token, account_id), "chatgpt usage API")
        except Exception as exc:
            errors.append(str(exc))
    elif cred_status == "missing":
        errors.append("未找到 ~/.codex/auth.json")
    elif cred_status == "expired":
        errors.append("Codex access token 已过期")
    else:
        errors.append("Codex 登录文件无效")

    try:
        return _provider_from_usage(_cli_usage(), "codex app-server")
    except Exception as exc:
        errors.append(str(exc))

    p.status = "error"
    p.error = "；".join(errors) or "Codex 额度不可用"
    p.remedy = "本机跑一次 codex login"
    return p
