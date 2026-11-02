"""GitHub-reviewed advisories -> per-package GHSA counts.

Source: a local clone of https://github.com/github/advisory-database (OSV-format JSON files
under advisories/github-reviewed/). A bulk clone avoids API rate limits.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .schema import SEVERITIES
from .timeutil import N_MONTHS, format_ts, month_index, parse_ts, to_dt

log = logging.getLogger(__name__)

HORIZON_DAYS = 90


@dataclass(frozen=True)
class Advisory:
    ghsa_id: str
    published: datetime
    severity: str  # low | moderate | high | critical | unknown


def normalize_severity(raw) -> str:
    s = str(raw or "").strip().lower()
    return s if s in {"low", "moderate", "high", "critical"} else "unknown"


def _load_cache(cache_path: Path, file_count: int):
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if data.get("file_count") != file_count:
            return None
        return {
            name: [Advisory(i, parse_ts(p), s) for i, p, s in items]
            for name, items in data["packages"].items()
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save_cache(cache_path: Path, file_count: int, result) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "file_count": file_count,
        "packages": {
            name: [[a.ghsa_id, format_ts(a.published), a.severity] for a in advs]
            for name, advs in result.items()
        },
    }
    cache_path.write_text(json.dumps(data), encoding="utf-8")


def load_npm_advisories(
    db_root: Path, cache_path: Path | None = None
) -> dict[str, list[Advisory]]:
    """package name -> advisories (distinct GHSA ids, withdrawn ones excluded).

    Reading every advisory file is slow (minutes on Windows), so the parsed npm result is
    cached in cache_path and reused while the number of advisory files is unchanged.
    Delete the cache file after pulling a newer advisory database.
    """
    base = Path(db_root) / "advisories" / "github-reviewed"
    if not base.is_dir():
        raise FileNotFoundError(
            f"{base} not found - clone github/advisory-database and pass its path"
        )
    files = sorted(base.rglob("GHSA-*.json"))
    log.info("found %d advisory files", len(files))
    if cache_path and cache_path.exists():
        cached = _load_cache(cache_path, len(files))
        if cached is not None:
            log.info("using cached advisories from %s", cache_path)
            return cached
    by_pkg: dict[str, dict[str, Advisory]] = {}
    skipped = 0
    for n, path in enumerate(files, 1):
        if n % 2000 == 0:
            log.info("read %d/%d advisory files", n, len(files))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("withdrawn"):
                continue
            ghsa_id = data.get("id") or path.stem
            published = parse_ts(data["published"])
        except (OSError, ValueError, KeyError):
            skipped += 1
            log.warning("skipping unreadable advisory %s", path)
            continue
        severity = normalize_severity((data.get("database_specific") or {}).get("severity"))
        for affected in data.get("affected") or []:
            pkg = affected.get("package") or {}
            if str(pkg.get("ecosystem", "")).lower() != "npm" or not pkg.get("name"):
                continue
            by_pkg.setdefault(pkg["name"], {})[ghsa_id] = Advisory(
                ghsa_id, published, severity
            )
    if skipped:
        log.warning("%d advisory files could not be read", skipped)
    result = {
        name: sorted(d.values(), key=lambda a: (a.published, a.ghsa_id))
        for name, d in by_pkg.items()
    }
    if cache_path:
        _save_cache(cache_path, len(files), result)
    return result


def ghsa_features(
    advisories: list[Advisory],
    prediction_date: date,
    months: list[tuple[date, date]],
) -> dict[str, int]:
    """Totals (< prediction), monthly series M0..M11, and the 90-day targets."""
    pred = to_dt(prediction_date)
    horizon_end = pred + timedelta(days=HORIZON_DAYS)
    out: dict[str, int] = {}
    for sev in SEVERITIES:
        out[f"ghsa_{sev}_count"] = 0
        out[f"target_ghsa_{sev}_count_90d"] = 0
        for i in range(N_MONTHS):
            out[f"ghsa_{sev}_count_M{i}"] = 0
    for adv in advisories:
        if adv.published < pred:
            out[f"ghsa_{adv.severity}_count"] += 1
            i = month_index(adv.published, months)
            if i is not None:
                out[f"ghsa_{adv.severity}_count_M{i}"] += 1
        elif adv.published < horizon_end:
            out[f"target_ghsa_{adv.severity}_count_90d"] += 1
    return out
