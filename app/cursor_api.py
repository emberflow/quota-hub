from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from .models import ModelRow, OFFICIAL, Provider, Window
from .timefmt import annotate_window, parse_ts


def _state_db() -> Path:
    override = os.environ.get("CURSOR_STATE_DB")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise FileNotFoundError("APPDATA missing")
    return Path(appdata) / "Cursor" / "User" / "globalStorage" / "state.vscdb"


def _read_item(key: str) -> Optional[str]:
    db = _state_db()
    if not db.exists():
        return None
    uri = db.as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        row = con.execute("SELECT value FROM ItemTable WHERE key = ?", (key,)).fetchone()
        if not row or row[0] is None:
            return None
        val = row[0]
        if isinstance(val, bytes):
            val = val.decode("utf-8", "ignore")
        return str(val)
    finally:
        con.close()


def _access_token() -> Optional[str]:
    return _read_item("cursorAuth/accessToken")


def _jwt_sub(token: str) -> Optional[str]:
    import base64

    try:
        part = token.split(".")[1]
        pad = "=" * (-len(part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(part + pad))
        return payload.get("sub")
    except Exception:
        return None


def _request(token: str, path: str, method: str = "GET", body: Any = None) -> dict:
    sub = _jwt_sub(token) or ""
    cookie = f"WorkosCursorSessionToken={sub}%3A%3A{token}" if sub else f"WorkosCursorSessionToken={token}"
    headers = {
        "Cookie": cookie,
        "Accept": "application/json",
        "User-Agent": "quota-hub/0.1",
        "Authorization": f"Bearer {token}",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Origin"] = "https://cursor.com"
    req = urllib.request.Request(
        "https://cursor.com" + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:200]
        raise RuntimeError(f"HTTP {exc.code} {path}: {detail}") from exc


def _pct_left(used: Optional[float]) -> Optional[float]:
    if used is None:
        return None
    return round(max(0.0, min(100.0, 100.0 - float(used))), 2)


def _window(wid: str, label: str, kind: str, used: Optional[float], resets) -> Window:
    left = _pct_left(used)
    extra = annotate_window(left, parse_ts(resets))
    return Window(
        id=wid,
        label=label,
        kind=kind,
        percent_remaining=left,
        percent_used=None if used is None else round(float(used), 2),
        extra="",
        **extra,
    )


def collect_cursor() -> Provider:
    p = Provider(
        id="cursor",
        label="Cursor Pro",
        official_url=OFFICIAL["cursor"],
        source="cursor.com dashboard API",
    )
    token = _access_token()
    if not token:
        p.status = "auth_required"
        p.error = "读不到 Cursor 本机登录"
        p.remedy = "在 Cursor 里保持登录后刷新看板"
        return p
    try:
        summary = _request(token, "/api/usage-summary")
        period = _request(token, "/api/dashboard/get-current-period-usage", "POST", {})
        me = _request(token, "/api/auth/me")
    except Exception as exc:
        p.status = "error"
        p.error = str(exc)
        p.remedy = "打开 Cursor 官网 Spending 页核对"
        return p

    p.plan = str(summary.get("membershipType") or "pro")
    p.status = "fresh"
    resets = summary.get("billingCycleEnd") or period.get("billingCycleEnd")
    plan = period.get("planUsage") or {}
    auto = plan.get("autoPercentUsed")
    api = plan.get("apiPercentUsed")
    total = plan.get("totalPercentUsed")
    p.windows = [
        _window("total", "含用量（总计）", "monthly", total, resets),
        _window("auto", "Cursor 模型 / Auto", "monthly", auto, resets),
        _window("api", "其它模型 / API", "monthly", api, resets),
    ]
    included = plan.get("includedSpend")
    limit = plan.get("limit")
    if included is not None and limit:
        p.windows[0].extra = f"含用量约 ${included/100:.0f} / ${limit/100:.0f}"

    user_id = me.get("id")
    start = period.get("billingCycleStart") or summary.get("billingCycleStart")
    end = period.get("billingCycleEnd") or summary.get("billingCycleEnd")
    start_ts = parse_ts(start)
    end_ts = parse_ts(end)
    if user_id and start_ts and end_ts:
        try:
            agg = _request(
                token,
                "/api/dashboard/get-aggregated-usage-events",
                "POST",
                {
                    "teamId": 0,
                    "startDate": str(int(start_ts.timestamp() * 1000)),
                    "endDate": str(int(end_ts.timestamp() * 1000)),
                    "userId": user_id,
                },
            )
            rows = agg.get("aggregations") or []
            models = []
            for r in rows:
                cents = float(r.get("totalCents") or 0)
                models.append(
                    ModelRow(
                        name=str(r.get("modelIntent") or "?"),
                        tokens_in=int(r.get("inputTokens") or 0),
                        tokens_out=int(r.get("outputTokens") or 0),
                        cost_usd=round(cents / 100.0, 4),
                    )
                )
            models.sort(key=lambda m: -(m.cost_usd or 0))
            p.models = models[:20]
        except Exception:
            pass
    return p
