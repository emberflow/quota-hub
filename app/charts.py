from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

from .cursor_api import collect_cursor_daily
from .logs import collect_codex_daily, collect_grok_daily
from .store import used_from_remaining

LABELS = {
    "cursor": "Cursor",
    "codex": "ChatGPT Plus",
    "grok": "SuperGrok",
    "antigravity": "Antigravity",
}


def _date_list(days: int) -> list[str]:
    today = date.today()
    return [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def _fill(days: list[str], values: dict[str, float]) -> list[dict[str, Any]]:
    return [{"date": d, "value": round(float(values.get(d, 0) or 0), 4)} for d in days]


def _safe_map(fn, days: int) -> list:
    try:
        return fn(days) or []
    except Exception:
        return []


def build_daily_charts(days: int = 14) -> list[dict[str, Any]]:
    span = _date_list(days)
    snap_used = used_from_remaining(days)

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_cursor = pool.submit(_safe_map, collect_cursor_daily, days)
        f_codex = pool.submit(_safe_map, collect_codex_daily, days)
        f_grok = pool.submit(_safe_map, collect_grok_daily, days)
        cursor_rows = f_cursor.result()
        codex_rows = f_codex.result()
        grok_rows = f_grok.result()

    cursor_usd: dict[str, float] = {}
    for row in cursor_rows:
        cursor_usd[row["date"]] = float(row.get("usd") or 0)

    codex_tok: dict[str, float] = {}
    for row in codex_rows:
        tok = float(row.get("tokensIn") or 0) + float(row.get("tokensOut") or 0)
        codex_tok[row["date"]] = codex_tok.get(row["date"], 0) + tok

    grok_tok: dict[str, float] = {}
    for row in grok_rows:
        grok_tok[row["date"]] = grok_tok.get(row["date"], 0) + float(row.get("tokensIn") or 0)

    cursor_values = cursor_usd
    cursor_unit = "usd"
    cursor_unit_label = "当日花费 $"
    cursor_source = "Cursor 用量事件（本周期账单）"
    if not any(cursor_usd.values()):
        cursor_values = snap_used.get("cursor", {})
        cursor_unit = "percent"
        cursor_unit_label = "当日剩余下降 %"
        cursor_source = "看板快照差值（跨天）"

    charts = [
        {
            "id": "cursor",
            "label": LABELS["cursor"],
            "unit": cursor_unit,
            "unitLabel": cursor_unit_label,
            "source": cursor_source,
            "days": _fill(span, cursor_values),
        },
        {
            "id": "codex",
            "label": LABELS["codex"],
            "unit": "tokens" if any(codex_tok.values()) else "percent",
            "unitLabel": "当日 token" if any(codex_tok.values()) else "当日剩余下降 %",
            "source": "本机 Codex 会话日志" if any(codex_tok.values()) else "看板快照差值（跨天）",
            "days": _fill(span, codex_tok if any(codex_tok.values()) else snap_used.get("codex", {})),
        },
        {
            "id": "grok",
            "label": LABELS["grok"],
            "unit": "percent" if not any(grok_tok.values()) else "tokens",
            "unitLabel": "当日 token" if any(grok_tok.values()) else "当日剩余下降 %",
            "source": "本机 Grok 会话" if any(grok_tok.values()) else "看板快照差值（跨天）",
            "days": _fill(span, grok_tok if any(grok_tok.values()) else snap_used.get("grok", {})),
        },
        {
            "id": "antigravity",
            "label": LABELS["antigravity"],
            "unit": "percent",
            "unitLabel": "当日剩余下降 %",
            "source": "看板快照差值（跨天；当天多次刷新才有）",
            "days": _fill(span, snap_used.get("antigravity", {})),
        },
    ]
    return charts
