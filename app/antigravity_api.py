from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from .models import ModelRow, OFFICIAL, Provider, Window
from .timefmt import annotate_window, parse_ts


def _npx() -> list[str]:
    if os.name == "nt":
        return ["cmd", "/c", "npx", "-y"]
    return ["npx", "-y"]


def _as_percent(value) -> float | None:
    if value is None:
        return None
    n = float(value)
    if 0 <= n <= 1.5:
        return round(n * 100.0, 2)
    return round(n, 2)


def collect_antigravity() -> Provider:
    p = Provider(
        id="antigravity",
        label="Google AI Pro · Antigravity",
        official_url=OFFICIAL["antigravity"],
        source="antigravity-usage",
    )
    try:
        proc = subprocess.run(
            _npx() + ["antigravity-usage", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
    except Exception as exc:
        p.status = "error"
        p.error = str(exc)
        p.remedy = "打开 Antigravity，或运行 antigravity-usage login"
        return p
    text = (proc.stdout or "").strip()
    start = text.find("{")
    start_arr = text.find("[")
    if start < 0 and start_arr < 0:
        err = (proc.stderr or text or "无输出").strip()
        if "not running" in err.lower() or "not logged in" in err.lower():
            err = "Antigravity 未在运行，也还没有 antigravity-usage login"
        p.status = "auth_required"
        p.error = err.splitlines()[-1][:240]
        p.remedy = "打开 Antigravity IDE，或运行: npx -y antigravity-usage login"
        return p
    payload: Any
    try:
        if start >= 0 and (start_arr < 0 or start < start_arr):
            payload = json.loads(text[start:])
        else:
            payload = json.loads(text[start_arr:])
    except json.JSONDecodeError:
        p.status = "error"
        p.error = "antigravity-usage JSON 解析失败"
        return p

    models = []
    plan = ""
    if isinstance(payload, dict):
        plan = str(payload.get("planType") or payload.get("plan") or "")
        models = payload.get("models") or payload.get("quota") or []
        if isinstance(payload.get("accounts"), list) and payload["accounts"]:
            first = payload["accounts"][0]
            plan = str(first.get("planType") or plan)
            models = first.get("models") or models
    elif isinstance(payload, list):
        models = payload

    p.plan = plan
    windows: list[Window] = []
    model_rows: list[ModelRow] = []
    gemini_left = None
    other_left = None
    gemini_reset = None
    other_reset = None

    if isinstance(models, list):
        for m in models:
            if not isinstance(m, dict):
                continue
            name = str(m.get("label") or m.get("modelId") or m.get("name") or "?")
            mid = str(m.get("modelId") or name).lower()
            left = _as_percent(
                m.get("remainingPercentage")
                if m.get("remainingPercentage") is not None
                else m.get("remainingFraction")
            )
            resets = m.get("resetTime") or m.get("resetsAt")
            model_rows.append(
                ModelRow(
                    name=name,
                    extra="" if left is None else f"剩余 {left:.0f}%",
                )
            )
            is_gemini = "gemini" in mid or "gemini" in name.lower()
            if is_gemini:
                if gemini_left is None or (left is not None and left < gemini_left):
                    gemini_left = left
                    gemini_reset = resets
            else:
                if other_left is None or (left is not None and left < other_left):
                    other_left = left
                    other_reset = resets

    if gemini_left is not None:
        meta = annotate_window(gemini_left, parse_ts(gemini_reset))
        windows.append(
            Window(
                id="gemini",
                label="Gemini 池",
                kind="session",
                percent_remaining=gemini_left,
                percent_used=None if gemini_left is None else round(100 - gemini_left, 2),
                extra="Flash / Pro 共用",
                **meta,
            )
        )
    if other_left is not None:
        meta = annotate_window(other_left, parse_ts(other_reset))
        windows.append(
            Window(
                id="other",
                label="非 Google 模型池",
                kind="session",
                percent_remaining=other_left,
                percent_used=None if other_left is None else round(100 - other_left, 2),
                extra="Claude / GPT 等",
                **meta,
            )
        )

    p.windows = windows
    p.models = model_rows[:30]
    if windows or model_rows:
        p.status = "fresh"
    else:
        p.status = "unavailable"
        p.error = "已连上但没有额度字段"
        p.remedy = "在 Antigravity 里打开 /usage 对照"
    return p
