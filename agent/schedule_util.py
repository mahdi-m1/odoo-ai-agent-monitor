"""Shared "is it due?" scheduling helpers for settings-driven periodic jobs (backups, email).

A settings dict provides: frequency (off|hourly|every6h|daily|weekly), hour, day, and a
last-run timestamp under a caller-specified key. Jobs poll is_due() on a short interval so the
user can change the schedule from the UI without restarting the scheduler.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

FREQUENCIES = ("off", "hourly", "every6h", "daily", "weekly")
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def period(frequency: str) -> Optional[timedelta]:
    return {"hourly": timedelta(hours=1), "every6h": timedelta(hours=6),
            "daily": timedelta(days=1), "weekly": timedelta(days=7)}.get(frequency)


def current_slot(s: Dict[str, Any], now: Optional[datetime] = None) -> Optional[datetime]:
    """Most recent scheduled time <= now (None when frequency is off)."""
    now = now or datetime.now()
    f = s.get("frequency", "off")
    if f == "off":
        return None
    if f in ("hourly", "every6h"):
        step = period(f)
        base = now.replace(minute=0, second=0, microsecond=0)
        return base - timedelta(hours=base.hour % int(step.total_seconds() // 3600))
    slot = now.replace(hour=int(s.get("hour", 0)), minute=0, second=0, microsecond=0)
    if f == "weekly":
        slot -= timedelta(days=(slot.weekday() - DAYS.index(s.get("day", "sunday"))) % 7)
    if slot > now:
        slot -= period(f)
    return slot


def is_due(s: Dict[str, Any], last_key: str, now: Optional[datetime] = None) -> bool:
    slot = current_slot(s, now)
    if slot is None:
        return False
    last = datetime.fromisoformat(s[last_key]) if s.get(last_key) else None
    return last is None or last < slot


def next_due(s: Dict[str, Any], last_key: str, now: Optional[datetime] = None) -> Optional[str]:
    now = now or datetime.now()
    slot = current_slot(s, now)
    if slot is None:
        return None
    nxt = slot if is_due(s, last_key, now) else slot + period(s["frequency"])
    return nxt.isoformat(timespec="minutes")
