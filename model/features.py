"""Version records of one package (official record schema) -> one feature dict.

Only schema columns are used, so the same code runs on training data and on the records
handed to the predictor. Target columns are never read here.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from urllib.parse import unquote

from npmdata.registry import semver_key
from npmdata.timeutil import N_MONTHS, parse_ts, to_dt

SEVERITIES = ["low", "moderate", "high", "critical"]  # the four predicted counts
PREDICTION_DATE = date(2026, 7, 1)

STATIC_COLUMNS = [
    "unpacked_size",
    "file_count",
    "dependency_count",
    "optional_dependency_count",
    "peer_dependency_count",
    "dev_dependency_count",
    "bundled_dependency_count",
]


def split_purl(purl: str) -> tuple[str, str]:
    """'pkg:npm/%40babel/core@7.0.0' -> ('@babel/core', '7.0.0')."""
    if not purl.startswith("pkg:npm/"):
        raise ValueError(f"not an npm purl: {purl!r}")
    path, _, version = purl[len("pkg:npm/"):].rpartition("@")
    return unquote(path), unquote(version)


def group_by_package(records) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        groups[split_purl(record["purl"])[0]].append(record)
    return dict(groups)


def _num(value):
    """Schema values arrive as int, None, or (from CSV) strings like '' and '12'."""
    if value is None or value == "":
        return None
    return float(value)


def _series(record: dict, metric: str) -> list:
    return [_num(record.get(f"{metric}_M{i}")) for i in range(N_MONTHS)]


def _sum(values, n: int):
    """Sum of the newest n months; None when none of them was observed."""
    seen = [v for v in values[:n] if v is not None]
    return sum(seen) if seen else None


def package_features(records: list[dict], prediction_date: date = PREDICTION_DATE) -> dict:
    """Features of one package. M0 is the month just before prediction_date."""
    if not records:
        raise ValueError("no records")
    versions = [(semver_key(split_purl(r["purl"])[1]), r) for r in records]
    latest = max(versions, key=lambda v: v[0])[1]  # highest semver, not newest timestamp
    pred = to_dt(prediction_date)
    published = [parse_ts(r["published_at"]) for r in records]

    f: dict = {
        "versions_in_window": len(records),
        "latest_major": max(v[0] for v in versions)[0],
        "package_age_days": (pred - parse_ts(latest["package_created_at"])).days,
        "days_since_last_release": (pred - max(published)).days,
    }
    for col in STATIC_COLUMNS:
        f[col] = _num(latest.get(col))

    releases = _series(latest, "package_stable_release_count")
    downloads = _series(latest, "package_downloads")
    for n in (1, 3, 12):
        f[f"releases_{n}m"] = _sum(releases, n)
        f[f"downloads_{n}m"] = _sum(downloads, n)

    # GHSA history: the highest value over all version records. When the counts are per package
    # every record has the same value, so this changes nothing. When they are per version (only
    # advisories affecting that version), the latest version often shows 0 because its
    # advisories are fixed, and the maximum recovers the package's history.
    f["ghsa_total"] = f["ghsa_12m"] = f["ghsa_3m"] = 0.0
    for sev in SEVERITIES + ["unknown"]:
        total = max(_num(r.get(f"ghsa_{sev}_count")) or 0.0 for r in records)
        monthly = [max(v or 0.0 for v in month)
                   for month in zip(*(_series(r, f"ghsa_{sev}_count") for r in records))]
        f[f"ghsa_{sev}_total"] = total
        f[f"ghsa_{sev}_12m"] = sum(monthly)
        f[f"ghsa_{sev}_6m"] = sum(monthly[:6])
        f[f"ghsa_{sev}_3m"] = sum(monthly[:3])
        f[f"ghsa_{sev}_older"] = max(0.0, total - sum(monthly))  # before the 12-month window
        f["ghsa_total"] += total
        f["ghsa_12m"] += sum(monthly)
        f["ghsa_3m"] += sum(monthly[:3])
    return f


def package_targets(records: list[dict]) -> dict[str, float]:
    """Observed 90-day counts (training data only)."""
    return {sev: _num(records[0][f"target_ghsa_{sev}_count_90d"]) or 0.0 for sev in SEVERITIES}
