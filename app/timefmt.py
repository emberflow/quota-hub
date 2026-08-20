from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        n = float(value)
        if n > 1e12:
            n = n / 1000.0
        elif n > 1e10:
            n = n / 1000.0
        return datetime.fromtimestamp(n, tz=timezone.utc)
    s = str(value).strip()
    if s.isdigit():
        return parse_ts(int(s))
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except ValueError:
        return None


def iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def remaining_label(resets_at: Optional[datetime], now: Optional[datetime] = None) -> str:
    if not resets_at:
        return "刷新时间未知"
    now = now or now_utc()
    secs = (resets_at - now).total_seconds()
    if secs <= 0:
        return "已到刷新点"
    days = int(secs // 86400)
    hours = int((secs % 86400) // 3600)
    mins = int((secs % 3600) // 60)
    if days >= 1:
        return f"{days} 天 {hours} 小时"
    if hours >= 1:
        return f"{hours} 小时 {mins} 分钟"
    return f"{mins} 分钟"


def hours_until(resets_at: Optional[datetime], now: Optional[datetime] = None) -> Optional[float]:
    if not resets_at:
        return None
    now = now or now_utc()
    return (resets_at - now).total_seconds() / 3600.0


def annotate_window(percent_remaining: Optional[float], resets_at: Optional[datetime], now=None) -> dict:
    now = now or now_utc()
    hrs = hours_until(resets_at, now)
    use_first = False
    urgency = None
    if percent_remaining is not None and hrs is not None and hrs > 0:
        urgency = round(percent_remaining / max(hrs, 0.25), 4)
        if percent_remaining > 20 and hrs < 48:
            use_first = True
    return {
        "remaining_label": remaining_label(resets_at, now),
        "hours_until_reset": None if hrs is None else round(hrs, 2),
        "urgency": urgency,
        "use_first": use_first,
        "resets_at": iso(resets_at),
    }
