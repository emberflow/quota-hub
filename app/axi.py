from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from .models import OFFICIAL, Provider, Window
from .timefmt import annotate_window, parse_ts


def _npx_cmd() -> list[str]:
    if os.name == "nt":
        return ["cmd", "/c", "npx", "-y"]
    return ["npx", "-y"]


def run_quota_axi(providers: str = "codex") -> dict[str, Any]:
    cmd = _npx_cmd() + ["quota-axi", "--json", "--provider", providers]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        cwd=os.environ.get("TEMP") or ".",
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(proc.stderr.strip() or f"quota-axi exit {proc.returncode}")
    text = proc.stdout.strip()
    start = text.find("{")
    if start < 0:
        raise RuntimeError("quota-axi 没有 JSON 输出")
    return json.loads(text[start:])


def collect_codex() -> Provider:
    p = Provider(
        id="codex",
        label="ChatGPT Plus · Codex",
        official_url=OFFICIAL["codex"],
        source="quota-axi",
    )
    try:
        data = run_quota_axi("codex")
    except Exception as exc:
        p.status = "error"
        p.error = str(exc)
        p.remedy = "确认已安装 Node，并且本机跑过 codex login"
        return p
    providers = data.get("providers") or []
    row = next((x for x in providers if x.get("provider") == "codex"), None)
    if not row:
        p.status = "unavailable"
        p.error = "quota-axi 未返回 Codex"
        return p
    p.plan = str(row.get("plan") or "")
    state = row.get("state") or {}
    p.status = state.get("status") or "unavailable"
    if p.status not in ("fresh", "stale"):
        p.error = state.get("error") or "Codex 额度不可用"
        p.remedy = state.get("remedyCommand") or "运行 codex login"
        if p.status == "stale":
            pass
        else:
            return p
    if p.status == "stale":
        p.status = "fresh"
        p.error = "数据可能略旧"
    credits = row.get("credits") or {}
    extra_credit = ""
    if credits.get("remaining") is not None:
        extra_credit = f"credits {credits['remaining']:.1f}"
    windows = []
    for w in row.get("windows") or []:
        left = w.get("percentRemaining")
        used = w.get("percentUsed")
        resets = parse_ts(w.get("resetsAt"))
        kind = w.get("kind") or "unknown"
        meta = annotate_window(left, resets)
        windows.append(
            Window(
                id=str(w.get("id") or kind),
                label=str(w.get("label") or kind),
                kind=kind,
                percent_remaining=left,
                percent_used=used,
                extra=extra_credit if w.get("id") == "weekly" else "",
                **meta,
            )
        )
    p.windows = windows
    if p.status not in ("fresh", "stale"):
        p.status = "fresh" if windows else "unavailable"
    return p
