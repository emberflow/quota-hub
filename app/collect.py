from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

from .antigravity_api import collect_antigravity
from .codex_api import collect_codex
from .charts import build_daily_charts
from .cursor_api import collect_cursor
from .grok_api import collect_grok
from .logs import local_daily
from .models import Provider
from .store import daily_deltas, record


_CACHE_TTL_SECONDS = 30.0
_cache_lock = threading.Condition()
_cache_value: dict | None = None
_cache_at = 0.0
_collecting = False


def collect_all() -> dict:
    providers: list[Provider] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {
            pool.submit(collect_cursor): "cursor",
            pool.submit(collect_codex): "codex",
            pool.submit(collect_grok): "grok",
            pool.submit(collect_antigravity): "antigravity",
        }
        by_id = {}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                by_id[key] = fut.result()
            except Exception as exc:
                from .models import OFFICIAL, Provider as P

                labels = {
                    "cursor": "Cursor Pro",
                    "codex": "ChatGPT Plus · Codex",
                    "grok": "SuperGrok / grokcli",
                    "antigravity": "Google AI Pro · Antigravity",
                }
                p = P(id=key, label=labels[key], official_url=OFFICIAL[key], status="error")
                p.error = str(exc)
                by_id[key] = p
        for key in ("cursor", "codex", "grok", "antigravity"):
            providers.append(by_id[key])

    snap_rows = []
    use_first = []
    for p in providers:
        for w in p.windows:
            snap_rows.append(
                {
                    "provider": p.id,
                    "window_id": w.id,
                    "percent_remaining": w.percent_remaining,
                }
            )
            if w.use_first:
                use_first.append(
                    {
                        "provider": p.label,
                        "window": w.label,
                        "percentRemaining": w.percent_remaining,
                        "remainingLabel": w.remaining_label,
                        "urgency": w.urgency,
                        "hoursUntilReset": w.hours_until_reset,
                    }
                )
    try:
        record(snap_rows)
    except Exception:
        pass

    # Highest leftover-per-hour first: those are the windows that expire soon with unused quota.
    use_first.sort(key=lambda x: -(x.get("urgency") or 0))
    ranked = []
    for p in providers:
        for w in p.windows:
            if w.percent_remaining is None:
                continue
            ranked.append(
                {
                    "providerId": p.id,
                    "provider": p.label,
                    "window": w.label,
                    "percentRemaining": w.percent_remaining,
                    "remainingLabel": w.remaining_label,
                    "urgency": w.urgency,
                    "useFirst": w.use_first,
                    "kind": w.kind,
                }
            )
    ranked.sort(key=lambda x: -(x.get("urgency") or 0))

    return {
        "providers": [p.to_dict() for p in providers],
        "useFirst": use_first,
        "ranked": ranked[:12],
        "dailySnapshots": daily_deltas(14),
        "dailyLogs": local_daily(14),
        "dailyCharts": build_daily_charts(14),
    }


def collect_cached(force: bool = False) -> dict:
    """Return a recent quota snapshot, with one in-flight collection at a time."""
    global _cache_at, _cache_value, _collecting

    requested_at = time.monotonic()
    with _cache_lock:
        while _collecting:
            _cache_lock.wait()
            # A concurrent caller has just refreshed the snapshot.  A forced
            # refresh should join that request rather than immediately starting
            # a second expensive provider poll.
            if _cache_value is not None and _cache_at >= requested_at:
                return _cache_value
        if not force and _cache_value is not None and requested_at - _cache_at < _CACHE_TTL_SECONDS:
            return _cache_value
        _collecting = True

    try:
        value = collect_all()
    except Exception:
        with _cache_lock:
            _collecting = False
            _cache_lock.notify_all()
        raise

    with _cache_lock:
        _cache_value = value
        _cache_at = time.monotonic()
        _collecting = False
        _cache_lock.notify_all()
        return _cache_value
