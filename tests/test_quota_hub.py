from __future__ import annotations

import json
import sqlite3
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import collect, store
from app.github_api import valid_ref, valid_repo
from app.grok_api import _refresh_access_token
from app.logs import collect_grok_daily


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.data_patch = patch.object(store, "DATA", self.data)
        self.db_patch = patch.object(store, "DB_PATH", self.data / "snapshots.db")
        self.data_patch.start()
        self.db_patch.start()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.data_patch.stop()
        self.temp.cleanup()

    def test_same_window_is_deduplicated_and_windows_stay_separate(self) -> None:
        rows = [
            {"provider": "cursor", "window_id": "total", "percent_remaining": 90},
            {"provider": "cursor", "window_id": "auto", "percent_remaining": 70},
        ]
        store.record(rows)
        store.record(rows)
        con = store.connect()
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 2)
        finally:
            con.close()

        now = datetime.now(timezone.utc)
        local_date = now.astimezone().date().isoformat()
        con = store.connect()
        try:
            con.execute("DELETE FROM snapshots")
            for window_id, first, last in (("total", 90, 80), ("auto", 70, 65)):
                con.execute(
                    "INSERT INTO snapshots (taken_at, local_date, provider, window_id, percent_remaining) VALUES (?, ?, ?, ?, ?)",
                    ((now - timedelta(minutes=2)).isoformat(), local_date, "cursor", window_id, first),
                )
                con.execute(
                    "INSERT INTO snapshots (taken_at, local_date, provider, window_id, percent_remaining) VALUES (?, ?, ?, ?, ?)",
                    (now.isoformat(), local_date, "cursor", window_id, last),
                )
            con.commit()
        finally:
            con.close()

        deltas = {row["windowId"]: row for row in store.daily_deltas(1)}
        self.assertEqual(deltas["total"]["usedPercent"], 10)
        self.assertEqual(deltas["auto"]["usedPercent"], 5)

    def test_old_snapshots_are_pruned_on_write(self) -> None:
        con = store.connect()
        try:
            con.execute(
                "INSERT INTO snapshots (taken_at, local_date, provider, window_id, percent_remaining) VALUES (?, ?, ?, ?, ?)",
                ("2000-01-01T00:00:00+00:00", "2000-01-01", "cursor", "total", 50),
            )
            con.commit()
        finally:
            con.close()
        store.record([{"provider": "cursor", "window_id": "total", "percent_remaining": 49}])
        con = store.connect()
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM snapshots WHERE local_date < '2020-01-01'").fetchone()[0], 0)
        finally:
            con.close()


class CollectorTests(unittest.TestCase):
    def test_grok_cumulative_log_uses_peak_value_once(self) -> None:
        with TemporaryDirectory() as root:
            sessions = Path(root) / "sessions"
            sessions.mkdir()
            (sessions / "updates.jsonl").write_text(
                "\n".join(json.dumps({"totalTokens": n}) for n in (10, 30, 25)),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"GROK_HOME": root}):
                rows = collect_grok_daily(14)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["tokensIn"], 30)

    def test_grok_rejects_untrusted_refresh_issuer(self) -> None:
        entry = {
            "refresh_token": "not-sent",
            "oidc_client_id": "client",
            "oidc_issuer": "https://example.invalid/steal",
        }
        self.assertIsNone(_refresh_access_token(entry))


class SecurityAndCacheTests(unittest.TestCase):
    def test_github_inputs_are_strict(self) -> None:
        self.assertTrue(valid_repo("owner/repository-name"))
        self.assertFalse(valid_repo("owner/repo?recursive=1"))
        self.assertFalse(valid_repo("owner/../repo"))
        self.assertTrue(valid_ref("feature/dashboard"))
        self.assertFalse(valid_ref("feature/../secret"))
        self.assertFalse(valid_ref("main?recursive=1"))

    def test_concurrent_cache_calls_share_one_collection(self) -> None:
        calls = 0
        call_lock = threading.Lock()

        def fake_collect() -> dict:
            nonlocal calls
            with call_lock:
                calls += 1
            time.sleep(0.05)
            return {"providers": []}

        with collect._cache_lock:
            collect._cache_value = None
            collect._cache_at = 0
            collect._collecting = False
        with patch.object(collect, "collect_all", side_effect=fake_collect):
            results: list[dict] = []
            threads = [threading.Thread(target=lambda: results.append(collect.collect_cached())) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(calls, 1)
        self.assertEqual(len(results), 4)


if __name__ == "__main__":
    unittest.main()
