from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .timefmt import now_utc

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "snapshots.db"


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
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
        for w in windows:
            if w.get("percent_remaining") is None:
                continue
            con.execute(
                """
                INSERT INTO snapshots (taken_at, local_date, provider, window_id, percent_remaining)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    taken,
                    local_date,
                    w["provider"],
                    w["window_id"],
                    float(w["percent_remaining"]),
                ),
            )
        con.commit()
    finally:
        con.close()


def daily_deltas(days: int = 14) -> list[dict[str, Any]]:
    """Earliest vs latest remaining% per local day → consumed that day."""
    con = connect()
    try:
        rows = con.execute(
            """
            SELECT local_date, provider, window_id,
                   MIN(percent_remaining) AS min_rem,
                   MAX(percent_remaining) AS max_rem,
                   (SELECT percent_remaining FROM snapshots s2
                    WHERE s2.local_date = snapshots.local_date
                      AND s2.provider = snapshots.provider
                      AND s2.window_id = snapshots.window_id
                    ORDER BY taken_at ASC LIMIT 1) AS first_rem,
                   (SELECT percent_remaining FROM snapshots s3
                    WHERE s3.local_date = snapshots.local_date
                      AND s3.provider = snapshots.provider
                      AND s3.window_id = snapshots.window_id
                    ORDER BY taken_at DESC LIMIT 1) AS last_rem
            FROM snapshots
            GROUP BY local_date, provider, window_id
            ORDER BY local_date DESC
            LIMIT ?
            """,
            (days * 20,),
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
