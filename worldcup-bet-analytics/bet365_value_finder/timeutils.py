from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def date_start_utc(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def date_end_utc(value: date) -> datetime:
    return datetime.combine(value, time(23, 59, 59), tzinfo=timezone.utc)


def iso_z_seconds(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def iso_z(value: datetime | None) -> str:
    if value is None:
        return ""
    return iso_z_seconds(value)


def local_date(value: datetime | None, timezone_name: str) -> date | None:
    if value is None:
        return None
    return value.astimezone(ZoneInfo(timezone_name)).date()
