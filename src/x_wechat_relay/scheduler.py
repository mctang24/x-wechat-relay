from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
SCHEDULED_HOURS = (8, 10, 12, 14, 16, 18)
RANDOM_WINDOW_SECONDS = 15 * 60
WEEKDAYS = (0, 1, 2, 3, 4)


def eastern_now() -> datetime:
    return datetime.now(EASTERN)


def as_eastern(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=EASTERN)
    return now.astimezone(EASTERN)


def next_base_check(now: datetime) -> datetime:
    now = as_eastern(now)
    for day_offset in range(8):
        day = now + timedelta(days=day_offset)
        if day.weekday() not in WEEKDAYS:
            continue
        for hour in SCHEDULED_HOURS:
            candidate = day.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > now:
                return candidate
    raise RuntimeError("No weekday check point found")


def seconds_until_next_check(now: datetime) -> int:
    now = as_eastern(now)
    return max(0, int((next_base_check(now) - now).total_seconds()))


def random_jitter_seconds() -> int:
    return random.randint(0, RANDOM_WINDOW_SECONDS)
