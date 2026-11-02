"""npm registry package document -> one row per stable version in the 12-month window."""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote

from .timeutil import N_MONTHS, format_ts, month_index, parse_ts, to_dt

# Stable = plain semver without a prerelease part (build metadata after '+' is allowed).
STABLE_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(\+[0-9A-Za-z.-]+)?$"
)


def is_stable(version: str) -> bool:
    return bool(STABLE_RE.match(version))


def semver_key(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split("+")[0].split(".")
    return int(major), int(minor), int(patch)


def make_purl(name: str, version: str) -> str:
    """pkg:npm/... with the scope '@' percent-encoded, e.g. pkg:npm/%40babel/core@7.0.0."""
    if name.startswith("@"):
        scope, _, pkg = name.partition("/")
        path = f"{quote(scope, safe='')}/{quote(pkg, safe='')}"
    else:
        path = quote(name, safe="")
    return f"pkg:npm/{path}@{quote(version, safe='')}"


def _key_count(value) -> int:
    return len(value) if isinstance(value, dict) else 0


def _bundled_count(meta: dict) -> int:
    bundled = meta.get("bundleDependencies", meta.get("bundledDependencies"))
    if bundled is True:  # npm: true means "bundle all dependencies"
        return _key_count(meta.get("dependencies"))
    if isinstance(bundled, list):
        return len({b for b in bundled if isinstance(b, str)})
    return 0


def _nonneg_int(value):
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def package_rows(
    doc: dict, prediction_date: date, months: list[tuple[date, date]]
) -> list[dict]:
    """Rows for stable versions published in [start of M11, prediction_date).

    Each row also carries the package-level stable-release counts per month.
    """
    times = doc.get("time") or {}
    created = format_ts(parse_ts(times["created"]))
    window_start, window_end = to_dt(months[-1][0]), to_dt(prediction_date)

    stable: dict[str, object] = {}
    for version in (doc.get("versions") or {}):
        if is_stable(version) and version in times:
            try:
                stable[version] = parse_ts(times[version])
            except ValueError:
                continue

    release_counts = {f"package_stable_release_count_M{i}": 0 for i in range(N_MONTHS)}
    for published in stable.values():
        i = month_index(published, months)
        if i is not None:
            release_counts[f"package_stable_release_count_M{i}"] += 1

    rows = []
    for version in sorted(stable, key=semver_key):
        published = stable[version]
        if not (window_start <= published < window_end):
            continue
        meta = doc["versions"][version]
        dist = meta.get("dist") or {}
        row = {
            "purl": make_purl(doc["name"], version),
            "package_created_at": created,
            "published_at": format_ts(published),
            "unpacked_size": _nonneg_int(dist.get("unpackedSize")),
            "file_count": _nonneg_int(dist.get("fileCount")),
            "dependency_count": _key_count(meta.get("dependencies")),
            "optional_dependency_count": _key_count(meta.get("optionalDependencies")),
            "peer_dependency_count": _key_count(meta.get("peerDependencies")),
            "dev_dependency_count": _key_count(meta.get("devDependencies")),
            "bundled_dependency_count": _bundled_count(meta),
        }
        row.update(release_counts)
        rows.append(row)
    return rows
