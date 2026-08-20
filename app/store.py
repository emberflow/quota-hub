from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .timefmt import now_utc

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "snapshots.db"


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            taken_at TEXT NOT NULL,
            local_date TEXT NOT NULL,
            provider TEXT NOT NULL,
            window_id TEXT NOT NULL,
            percent_remaining REAL
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_snap ON snapshots(provider, window_id, local_date)"
    )
    return con


def record(windows: list[dict[str, Any]]) -> None:
    now = now_utc()
    local_date = now.astimezone().date().isoformat()
    taken = now.isoformat()
    con = connect()
    try:
        cutoff = (date.fromisoformat(local_date) - timedelta(days=90)).isoformat()
        con.execute("DELETE FROM snapshots WHERE local_date < ?", (cutoff,))
        for w in windows:
            if w.get("percent_remaining") is None:
                continue
            provider = w["provider"]
            window_id = w["window_id"]
            remaining = float(w["percent_remaining"])
            previous = con.execute(
                """
                SELECT percent_remaining FROM snapshots
                WHERE local_date = ? AND provider = ? AND window_id = ?
                ORDER BY taken_at DESC, id DESC LIMIT 1
                """,
                (local_date, provider, window_id),
            ).fetchone()
            # Repeated polling of an unchanged window adds no daily-use signal.
            if previous is not None and previous["percent_remaining"] == remaining:
                continue
            con.execute(
                """
                INSERT INTO snapshots (taken_at, local_date, provider, window_id, percent_remaining)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    taken,
                    local_date,
                    provider,
                    window_id,
                    remaining,
                ),
            )
        con.commit()
    finally:
        con.close()


def daily_deltas(days: int = 14) -> list[dict[str, Any]]:
    """Earliest vs latest remaining% per local day → consumed that day."""
    con = connect()
    try:
        cutoff = (date.today() - timedelta(days=max(days - 1, 0))).isoformat()
        rows = con.execute(
            """
            WITH ranked AS (
                SELECT local_date, provider, window_id, percent_remaining,
                       ROW_NUMBER() OVER (
                           PARTITION BY local_date, provider, window_id
                           ORDER BY taken_at ASC, id ASC
                       ) AS first_rank,
                       ROW_NUMBER() OVER (
                           PARTITION BY local_date, provider, window_id
                           ORDER BY taken_at DESC, id DESC
                       ) AS last_rank
                FROM snapshots
                WHERE local_date >= ?
            )
            SELECT local_date, provider, window_id,
                   MAX(CASE WHEN first_rank = 1 THEN percent_remaining END) AS first_rem,
                   MAX(CASE WHEN last_rank = 1 THEN percent_remaining END) AS last_rem
            FROM ranked
            GROUP BY local_date, provider, window_id
            ORDER BY local_date DESC
            """,
            (cutoff,),
        ).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        first = r["first_rem"]
        last = r["last_rem"]
        used = None
        if first is not None and last is not None:
            used = round(max(0.0, first - last), 2)
        out.append(
            {
                "date": r["local_date"],
                "provider": r["provider"],
                "windowId": r["window_id"],
                "firstRemaining": first,
                "lastRemaining": last,
                "usedPercent": used,
                "source": "snapshot",
            }
        )
    return out


PRIMARY_WINDOW = {
    "cursor": "total",
    "codex": "weekly",
    "grok": "weekly",
    "antigravity": "gemini",
}


def last_remaining_by_day(days: int = 14) -> list[dict[str, Any]]:
    """Latest remaining% recorded for each provider's primary window per local day."""
    cutoff = (date.today() - timedelta(days=days + 1)).isoformat()
    con = connect()
    try:
        rows = con.execute(
            """
            SELECT local_date, provider, window_id, percent_remaining, taken_at
            FROM snapshots
            WHERE local_date >= ?
            ORDER BY taken_at ASC
            """,
            (cutoff,),
        ).fetchall()
    finally:
        con.close()
    latest: dict[tuple[str, str, str], float] = {}
    for r in rows:
        latest[(r["local_date"], r["provider"], r["window_id"])] = r["percent_remaining"]
    out = []
    for (d, provider, window_id), rem in sorted(latest.items()):
        out.append(
            {
                "date": d,
                "provider": provider,
                "windowId": window_id,
                "lastRemaining": rem,
            }
        )
    return out


def used_from_remaining(days: int = 14) -> dict[str, dict[str, float]]:
    """provider -> {date: used percent} using previous day's last remaining."""
    series = last_remaining_by_day(days)
    by_pw: dict[tuple[str, str], dict[str, float]] = {}
    for row in series:
        key = (row["provider"], row["windowId"])
        by_pw.setdefault(key, {})[row["date"]] = row["lastRemaining"]
    used: dict[str, dict[str, float]] = {}
    for (provider, window_id), day_map in by_pw.items():
        if PRIMARY_WINDOW.get(provider) and window_id != PRIMARY_WINDOW[provider]:
            continue
        dates = sorted(day_map)
        prev = None
        for d in dates:
            rem = day_map[d]
            drop = 0.0
            if prev is not None and rem <= prev:
                drop = round(max(0.0, prev - rem), 2)
            used.setdefault(provider, {})[d] = drop
            prev = rem
    return used
