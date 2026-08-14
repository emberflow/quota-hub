from __future__ import annotations

import json
import os
import struct
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple

from .models import OFFICIAL, Provider, Window
from .timefmt import annotate_window, parse_ts


CONSUMER_URL = "https://grok.com/grok_api_v2.GrokBuildBilling/GetGrokCreditsConfig"


def _auth_path() -> Path:
    override = os.environ.get("GROK_AUTH_PATH")
    if override:
        return Path(override)
    home = os.environ.get("GROK_HOME")
    if home:
        return Path(home) / "auth.json"
    return Path.home() / ".grok" / "auth.json"


def _session_entry(data: dict) -> Optional[dict]:
    best = None
    for key, val in data.items():
        if not isinstance(val, dict):
            continue
        mode = str(val.get("auth_mode") or val.get("authMode") or "").lower()
        if mode != "oidc" and "auth.x.ai" not in str(key):
            continue
        if not val.get("key") and not val.get("refresh_token"):
            continue
        best = val
        if val.get("refresh_token"):
            return val
    return best


def _expired(entry: dict) -> bool:
    exp = parse_ts(entry.get("expires_at") or entry.get("expiresAt"))
    if not exp:
        return False
    return exp.timestamp() <= datetime.now(timezone.utc).timestamp() + 30


def _refresh_access_token(entry: dict) -> Optional[str]:
    """In-memory OIDC refresh. Never writes ~/.grok/auth.json."""
    refresh = entry.get("refresh_token")
    client_id = entry.get("oidc_client_id")
    issuer = str(entry.get("oidc_issuer") or "https://auth.x.ai").rstrip("/")
    if not refresh or not client_id:
        return None
    urls = [
        issuer + "/oauth/token",
        issuer + "/oauth2/token",
        "https://auth.x.ai/oauth/token",
    ]
    form = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
        }
    ).encode()
    for url in urls:
        req = urllib.request.Request(
            url,
            data=form,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8", "ignore"))
            token = payload.get("access_token")
            if token:
                return token
        except Exception:
            continue
    return None


def _grpc_call(token: str) -> bytes:
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                CONSUMER_URL,
                data=b"\x00\x00\x00\x00\x00",
                method="POST",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/grpc-web+proto",
                    "Accept": "application/grpc-web+proto",
                    "X-Grpc-Web": "1",
                    "Origin": "https://grok.com",
                    "Referer": "https://grok.com/",
                    "User-Agent": "quota-hub/0.1",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                return _decode_grpc_web(resp.read(), dict(resp.headers.items()))
        except Exception as exc:
            last = exc
            if attempt < 2:
                import time

                time.sleep(0.8)
    raise last


def _decode_grpc_web(body: bytes, headers: dict) -> bytes:
    messages: list[bytes] = []
    trailers: dict[str, str] = {}
    i = 0
    while i < len(body):
        if i + 5 > len(body):
            break
        flags = body[i]
        length = int.from_bytes(body[i + 1 : i + 5], "big")
        i += 5
        payload = body[i : i + length]
        i += length
        if flags & 0x80:
            text = payload.decode("utf-8", "replace")
            for line in text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    trailers[k.strip().lower()] = v.strip()
        else:
            messages.append(payload)
    status = trailers.get("grpc-status")
    if status and status != "0":
        raise RuntimeError(f"gRPC {status}")
    if not messages:
        raise RuntimeError("empty gRPC body")
    return b"".join(messages)


def _read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    value = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if not (b & 0x80):
            return value, pos
        shift += 7


def _iter_fields(data: bytes) -> Iterable[Tuple[int, int, Any]]:
    pos = 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        number = key >> 3
        wire = key & 0x07
        if wire == 0:
            value, pos = _read_varint(data, pos)
            yield number, wire, value
        elif wire == 1:
            yield number, wire, data[pos : pos + 8]
            pos += 8
        elif wire == 2:
            length, pos = _read_varint(data, pos)
            yield number, wire, data[pos : pos + length]
            pos += length
        elif wire == 5:
            yield number, wire, data[pos : pos + 4]
            pos += 4
        else:
            break


def _first_msg(data: bytes, field_no: int) -> Optional[bytes]:
    for number, wire, value in _iter_fields(data):
        if number == field_no and wire == 2:
            return value
    return None


def _parse_ts_msg(message: Optional[bytes]):
    if not message:
        return None
    seconds = 0
    nanos = 0
    for number, wire, value in _iter_fields(message):
        if number == 1 and wire == 0:
            seconds = int(value)
        elif number == 2 and wire == 0:
            nanos = int(value)
    if not seconds:
        return None
    return datetime.fromtimestamp(seconds + nanos / 1e9, timezone.utc)


def parse_credits(payload: bytes) -> dict:
    config = _first_msg(payload, 1)
    if config is None:
        raise RuntimeError("no config field")
    used_pct = 0.0
    start = end = None
    for number, wire, value in _iter_fields(config):
        if number == 1 and wire == 5:
            used_pct = struct.unpack("<f", value)[0]
        elif number == 4 and wire == 2:
            start = _parse_ts_msg(value)
        elif number == 5 and wire == 2:
            end = _parse_ts_msg(value)
    return {"percent_used": used_pct, "start": start, "end": end}


def collect_grok() -> Provider:
    p = Provider(
        id="grok",
        label="SuperGrok / grokcli",
        official_url=OFFICIAL["grok"],
        source="grok.com GetGrokCreditsConfig",
        plan="SuperGrok",
    )
    path = _auth_path()
    if not path.exists():
        p.status = "auth_required"
        p.error = "没有 ~/.grok/auth.json"
        p.remedy = "打开 grok CLI 登录一次"
        return p
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        p.status = "error"
        p.error = f"读 auth.json 失败: {exc}"
        return p
    entry = _session_entry(data) if isinstance(data, dict) else None
    if not entry:
        p.status = "auth_required"
        p.error = "auth.json 里没有 OIDC 会话"
        p.remedy = "打开 grok CLI 登录"
        return p
    token = entry.get("key")
    last_err = ""
    if token:
        try:
            parsed = parse_credits(_grpc_call(token))
            return _ok(p, parsed)
        except Exception as exc:
            last_err = str(exc)
    refreshed = _refresh_access_token(entry)
    if refreshed:
        try:
            parsed = parse_credits(_grpc_call(refreshed))
            return _ok(p, parsed)
        except Exception as exc:
            last_err = str(exc)
    p.status = "auth_required"
    p.error = last_err or "Grok 登录已过期"
    p.remedy = "打开 grok CLI 一次以刷新会话（看板不会改 auth.json）"
    return p


def _ok(p: Provider, parsed: dict) -> Provider:
    used = float(parsed["percent_used"] or 0)
    left = round(max(0.0, 100.0 - used), 2)
    extra = annotate_window(left, parsed["end"])
    p.status = "fresh"
    p.windows = [
        Window(
            id="weekly",
            label="共享周额度池",
            kind="weekly",
            percent_remaining=left,
            percent_used=round(used, 2),
            extra="Chat / Build / grokcli 共用",
            **extra,
        )
    ]
    return p
