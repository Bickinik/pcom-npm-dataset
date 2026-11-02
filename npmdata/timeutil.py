"""Time helpers. All timestamps are UTC; month windows are start-inclusive, end-exclusive."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

N_MONTHS = 12

_TS = re.compile(
    r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$"
)


def parse_ts(value: str) -> datetime:
    """Parse an RFC 3339 / ISO 8601 timestamp into an aware UTC datetime."""
    m = _TS.match(value.strip())
    if not m:
        raise ValueError(f"unsupported timestamp: {value!r}")
    day, clock, frac, tz = m.groups()
    micro = int(((frac or "") + "000000")[:6])
    dt = datetime.fromisoformat(f"{day}T{clock}").replace(
        microsecond=micro, tzinfo=timezone.utc
    )
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        dt -= sign * timedelta(hours=int(tz[1:3]), minutes=int(tz[4:6]))
    return dt


def format_ts(dt: datetime) -> str:
    """RFC 3339, UTC, exactly three fractional digits (as the record schema requires)."""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def to_dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def month_ranges(prediction_date: date, n: int = N_MONTHS) -> list[tuple[date, date]]:
    """[M0, M1, ..., M(n-1)] as (start, end) with end exclusive.

    M0 is the month that ends at prediction_date (June 2026 for 2026-07-01).
    """
    if prediction_date.day != 1:
        raise ValueError("prediction_date must be the first day of a month")
    out = []
    end = prediction_date
    for _ in range(n):
        start = (end - timedelta(days=1)).replace(day=1)
        out.append((start, end))
        end = start
    return out


def month_index(dt: datetime, months: list[tuple[date, date]]) -> int | None:
    for i, (start, end) in enumerate(months):
        if to_dt(start) <= dt < to_dt(end):
            return i
    return None
