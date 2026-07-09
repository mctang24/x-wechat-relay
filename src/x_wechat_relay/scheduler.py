from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
CRON_EXPRESSION = "0 9-17/2 * * *"
SCHEDULED_TIMES = tuple((hour, 0) for hour in range(9, 18, 2))


def schedule_now() -> datetime:
    return datetime.now(EASTERN)


def as_schedule_time(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=EASTERN)
    return now.astimezone(EASTERN)


def next_cron_time(after: datetime) -> datetime:
    after = as_schedule_time(after)
    for day_offset in range(2):
        day = after + timedelta(days=day_offset)
        for hour, minute in SCHEDULED_TIMES:
            candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > after:
                return candidate
    raise RuntimeError("No cron time found")


def seconds_until_next_check(now: datetime) -> int:
    now = as_schedule_time(now)
    return max(0, int((next_cron_time(now) - now).total_seconds()))
