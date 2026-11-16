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


def purl_parts(record: dict) -> tuple[str, tuple[int, int, int]] | None:
    """(package name, semver key) of a record; None when it has no usable purl."""
    try:
        name, version = split_purl((record.get("purl") or "").strip())
        return (name, semver_key(version)) if name else None
    except ValueError:
        return None


def group_by_package(records) -> dict[str, list[dict]]:
    """Records per package name. Records without a usable purl are grouped by the package's
    creation timestamp, which is the same on every version record of a package."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        parts = purl_parts(record)
        name = parts[0] if parts else f"(no purl, created {record.get('package_created_at') or '?'})"
        groups[name].append(record)
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
    pred = to_dt(prediction_date)
    published = [parse_ts(r["published_at"]) for r in records]
    parts = [purl_parts(r) for r in records]
    if all(parts):  # highest semver, not newest timestamp
        versions = [p[1] for p in parts]
        latest = records[versions.index(max(versions))]
        latest_major = max(versions)[0]
    else:  # version numbers unknown: the newest release stands in for the latest version
        latest = records[published.index(max(published))]
        latest_major = None

    f: dict = {
        "versions_in_window": len(records),
        "latest_major": latest_major,
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
    f.update(github_features(latest))
    return f


GITHUB_PREFIX = "gh_"  # all features from the optional issue / PR columns start with this


def github_features(record: dict) -> dict:
    """Issue and pull request activity of the package's repository; None where not filled."""
    series = {name: _series(record, name) for name in (
        "issues_opened", "issues_closed", "pull_requests_opened", "pull_requests_merged",
        "pull_requests_closed_unmerged", "issues_open_at_month_end",
        "pull_requests_open_at_month_end")}
    f = {}
    for short, name in (("issues_opened", "issues_opened"), ("prs_opened", "pull_requests_opened")):
        f[f"gh_{short}_3m"] = _sum(series[name], 3)
        f[f"gh_{short}_12m"] = _sum(series[name], 12)
    f["gh_issues_closed_12m"] = _sum(series["issues_closed"], 12)
    f["gh_prs_merged_12m"] = _sum(series["pull_requests_merged"], 12)
    f["gh_prs_unmerged_12m"] = _sum(series["pull_requests_closed_unmerged"], 12)
    f["gh_issues_open"] = series["issues_open_at_month_end"][0]
    f["gh_prs_open"] = series["pull_requests_open_at_month_end"][0]
    opened, closed = f["gh_issues_opened_12m"], f["gh_issues_closed_12m"]
    f["gh_issue_close_ratio"] = closed / opened if opened and closed is not None else None
    return f


def package_targets(records: list[dict]) -> dict[str, float]:
    """Observed 90-day counts (training data only)."""
    return {sev: _num(records[0][f"target_ghsa_{sev}_count_90d"]) or 0.0 for sev in SEVERITIES}
