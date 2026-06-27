from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), name="IST")
SCENARIO_NOW = datetime(2026, 4, 13, 3, 0, tzinfo=UTC)


def parse_utc(value: str) -> datetime:
    normalized_value = value.strip()
    if normalized_value.endswith("Z"):
        normalized_value = normalized_value[:-1] + "+00:00"
    return datetime.fromisoformat(normalized_value).astimezone(UTC)


def to_ist(value: datetime) -> datetime:
    return value.astimezone(IST)


def ist_datetime(month: int, day: int, hour: int = 18, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=IST).astimezone(UTC)


def end_of_day_ist(value: datetime) -> datetime:
    local_value = to_ist(value)
    return datetime.combine(local_value.date(), time(18, 0), tzinfo=IST).astimezone(UTC)


def start_of_day_ist(value: datetime) -> datetime:
    local_value = to_ist(value)
    return datetime.combine(local_value.date(), time.min, tzinfo=IST).astimezone(UTC)


def next_day_start_ist(value: datetime) -> datetime:
    return start_of_day_ist(value) + timedelta(days=1)


def format_ist(value: datetime) -> str:
    local_value = to_ist(value)
    return local_value.strftime("%b %-d %H:%M IST")


def format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
