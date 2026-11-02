"""Daily npm download counts -> monthly sums and observed-day counts."""
from __future__ import annotations

from datetime import date

from .timeutil import N_MONTHS


def monthly_downloads(daily: list[dict] | None, months: list[tuple[date, date]]) -> dict:
    """Sum only the days actually observed; never extrapolate.

    daily is the 'downloads' list of the npm range API ([{"day": ..., "downloads": ...}]),
    or None when the request failed (then every value is NULL).
    A month with no observed day gets downloads NULL and days_observed 0.
    """
    out: dict[str, int | None] = {}
    if daily is None:
        for i in range(N_MONTHS):
            out[f"package_downloads_M{i}"] = None
            out[f"package_download_days_observed_M{i}"] = None
        return out

    seen: list[dict[date, int]] = [{} for _ in months]
    for entry in daily:
        try:
            day = date.fromisoformat(entry["day"])
            count = int(entry["downloads"])
        except (KeyError, ValueError, TypeError):
            continue
        if count < 0:
            continue
        for i, (start, end) in enumerate(months):
            if start <= day < end:
                seen[i][day] = count
    for i in range(N_MONTHS):
        n_days = len(seen[i])
        out[f"package_download_days_observed_M{i}"] = n_days
        out[f"package_downloads_M{i}"] = sum(seen[i].values()) if n_days else None
    return out
