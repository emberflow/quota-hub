from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


def _home() -> Path:
    return Path.home()


def _iter_jsonl(path: Path) -> Iterator[dict]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line[0] not in "{[":
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def _day(ts) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        n = float(ts)
        if n > 1e12:
            n /= 1000.0
        dt = datetime.fromtimestamp(n, tz=timezone.utc).astimezone()
        return dt.date().isoformat()
    s = str(ts)
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).astimezone().date().isoformat()
    except ValueError:
        if len(s) >= 10 and s[4] == "-":
            return s[:10]
    return None


def _walk_recent(root: Path, pattern: str, days: int) -> list[Path]:
    if not root.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days + 1)
    cutoff_ts = cutoff.timestamp()
    out = []
    for p in root.rglob(pattern):
        try:
            if p.stat().st_mtime >= cutoff_ts:
                out.append(p)
        except OSError:
            continue
    return out


def collect_codex_daily(days: int = 14) -> list[dict[str, Any]]:
    root = Path(os.environ.get("CODEX_HOME") or (_home() / ".codex")) / "sessions"
    buckets: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"in": 0, "out": 0, "events": 0}
    )
    for path in _walk_recent(root, "*.jsonl", days):
        file_day = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
        session_in = session_out = 0
        saw_total = False
        last_day = file_day
        last_model = "codex"
        for obj in _iter_jsonl(path):
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            total = (
                payload.get("total_token_usage")
                or info.get("total_token_usage")
                or {}
            )
            turn = (
                payload.get("last_token_usage")
                or payload.get("token_usage")
                or {}
            )
            usage = total if isinstance(total, dict) and (
                total.get("input_tokens") or total.get("inputTokens")
            ) else turn
            if not isinstance(usage, dict):
                continue
            tin = int(usage.get("input_tokens") or usage.get("inputTokens") or 0)
            tout = int(usage.get("output_tokens") or usage.get("outputTokens") or 0)
            if tin == 0 and tout == 0:
                continue
            last_day = _day(payload.get("timestamp") or obj.get("timestamp")) or file_day
            last_model = str(
                payload.get("model") or obj.get("model") or usage.get("model") or last_model
            )
            if usage is total:
                saw_total = True
                session_in, session_out = tin, tout
            elif not saw_total:
                session_in += tin
                session_out += tout
        if session_in == 0 and session_out == 0:
            continue
        b = buckets[(last_day, last_model)]
        b["in"] += session_in
        b["out"] += session_out
        b["events"] += 1
    rows = []
    for (day, model), b in sorted(buckets.items()):
        rows.append(
            {
                "date": day,
                "provider": "codex",
                "windowId": model,
                "tokensIn": b["in"],
                "tokensOut": b["out"],
                "events": b["events"],
                "source": "codex-sessions",
            }
        )
    return rows


def collect_grok_daily(days: int = 14) -> list[dict[str, Any]]:
    root = Path(os.environ.get("GROK_HOME") or (_home() / ".grok")) / "sessions"
    if not root.exists():
        return []
    buckets: dict[str, int] = defaultdict(int)
    for path in _walk_recent(root, "*", days):
        if path.suffix not in {".json", ".jsonl"} and path.name not in {
            "summary.json",
            "signals.json",
            "updates.jsonl",
        }:
            continue
        day = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
        tokens = 0
        if path.suffix == ".jsonl" or path.name == "updates.jsonl":
            for obj in _iter_jsonl(path):
                tokens += int(
                    obj.get("totalTokens")
                    or obj.get("total_tokens")
                    or (obj.get("usage") or {}).get("totalTokens")
                    or 0
                )
        else:
            try:
                obj = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            if isinstance(obj, dict):
                tokens += int(
                    obj.get("totalTokens")
                    or obj.get("total_tokens")
                    or (obj.get("usage") or {}).get("totalTokens")
                    or 0
                )
        if tokens:
            buckets[day] += tokens
    return [
        {
            "date": day,
            "provider": "grok",
            "windowId": "local-context",
            "tokensIn": n,
            "tokensOut": 0,
            "source": "grok-sessions",
        }
        for day, n in sorted(buckets.items())
    ]


def local_daily(days: int = 14) -> list[dict[str, Any]]:
    return collect_codex_daily(days) + collect_grok_daily(days)
