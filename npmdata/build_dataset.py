"""Build the training dataset (SQLite + CSV) following the PCOM-01 record schema.

Example:
    python -m npmdata.build_dataset --advisory-db ../advisory-database \
        --extra-packages extra_packages.txt --workers 4
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import advisories, downloads, registry, schema, sources
from .timeutil import month_ranges

log = logging.getLogger("build")


def read_package_list(path: Path | None) -> list[str]:
    if not path:
        return []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def run(
    advisory_root: Path,
    out_dir: Path,
    prediction_date: date,
    extra_packages: list[str],
    fetch_registry,
    fetch_downloads,
    workers: int = 4,
    max_packages: int | None = None,
    cache_dir: Path | None = None,
    n_advisory: int | None = None,
    n_extra: int | None = None,
    seed: int = 42,
    skip_downloads: bool = False,
) -> dict:
    months = month_ranges(prediction_date)
    first_day, last_day = months[-1][0], sources.last_day_before(prediction_date)

    advisories_by_pkg = advisories.load_npm_advisories(
        advisory_root, cache_dir / "advisories_npm.json" if cache_dir else None
    )
    adv_names = sorted(advisories_by_pkg)
    extra_names = sorted(set(extra_packages) - set(adv_names))
    rng = random.Random(seed)
    if n_advisory is not None:  # seeded random subsets, e.g. for a smaller first version
        adv_names = sorted(rng.sample(adv_names, min(n_advisory, len(adv_names))))
    if n_extra is not None:
        extra_names = sorted(rng.sample(extra_names, min(n_extra, len(extra_names))))
    names = sorted(set(adv_names) | set(extra_names))
    if max_packages:
        names = names[:max_packages]
    log.info("%d advisory packages, %d total to fetch", len(advisories_by_pkg), len(names))

    def process(name: str):
        try:
            doc = fetch_registry(name)
            if not doc:
                return name, [], "not found in registry"
            rows = registry.package_rows(doc, prediction_date, months)
            if not rows:
                return name, [], None  # no stable release in the window
            note = None
            daily = None
            if not skip_downloads:
                try:
                    daily = fetch_downloads(name, first_day, last_day)
                except Exception as exc:  # downloads are optional: keep the rows, store NULLs
                    note = f"downloads unavailable: {type(exc).__name__}: {exc}"
            package_level = downloads.monthly_downloads(daily, months)
            package_level.update(
                advisories.ghsa_features(
                    advisories_by_pkg.get(name, []), prediction_date, months
                )
            )
            for row in rows:
                row.update(package_level)
            return name, rows, note
        except Exception as exc:  # keep going; the error log lists what to retry
            return name, [], f"{type(exc).__name__}: {exc}"

    all_rows: list[dict] = []
    errors: list[str] = []
    packages_with_rows = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, (name, rows, error) in enumerate(pool.map(process, names), 1):
            if error:
                errors.append(f"{name}\t{error}")
            if rows:
                packages_with_rows += 1
                all_rows.extend(rows)
            if n % 100 == 0:
                log.info("%d/%d packages, %d rows", n, len(names), len(all_rows))

    out_dir.mkdir(parents=True, exist_ok=True)
    names_cols = schema.column_names()
    db_path = out_dir / "training_data.sqlite"
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(schema.ddl(prediction_date))
    placeholders = ", ".join(f":{c}" for c in names_cols)
    con.executemany(
        f"INSERT INTO {schema.TABLE} ({', '.join(names_cols)}) VALUES ({placeholders})",
        [{c: row.get(c) for c in names_cols} for row in all_rows],
    )
    con.commit()
    con.close()

    with open(out_dir / "training_data.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(names_cols)
        for row in all_rows:
            writer.writerow(["" if row.get(c) is None else row[c] for c in names_cols])

    (out_dir / "errors.tsv").write_text("\n".join(errors) + ("\n" if errors else ""))
    info = {
        "prediction_date": prediction_date.isoformat(),
        "target_window": [
            prediction_date.isoformat(),
            (prediction_date + timedelta(days=advisories.HORIZON_DAYS)).isoformat(),
        ],
        "rows": len(all_rows),
        "packages_with_rows": packages_with_rows,
        "packages_requested": len(names),
        "packages_with_advisories": len(advisories_by_pkg),
        "errors": len(errors),
        "built_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (out_dir / "build_info.json").write_text(json.dumps(info, indent=2) + "\n")
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--advisory-db", required=True, type=Path,
                        help="local clone of github/advisory-database")
    parser.add_argument("--extra-packages", type=Path,
                        help="text file with one npm package name per line (no-advisory sample)")
    parser.add_argument("--prediction-date", default="2026-07-01", type=date.fromisoformat)
    parser.add_argument("--out", default=Path("data"), type=Path)
    parser.add_argument("--cache", default=Path("cache"), type=Path)
    parser.add_argument("--workers", default=4, type=int)
    parser.add_argument("--max-packages", type=int, help="smoke test only: biases the sample")
    parser.add_argument("--n-advisory", type=int,
                        help="random subset of this many packages that have advisories")
    parser.add_argument("--n-extra", type=int,
                        help="random subset of this many packages from --extra-packages")
    parser.add_argument("--seed", default=42, type=int, help="seed for the random subsets")
    parser.add_argument("--skip-downloads", action="store_true",
                        help="do not query the downloads API (download columns stay NULL)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    info = run(
        args.advisory_db,
        args.out,
        args.prediction_date,
        read_package_list(args.extra_packages),
        sources.registry_fetcher(args.cache),
        sources.downloads_fetcher(args.cache),
        args.workers,
        args.max_packages,
        args.cache,
        args.n_advisory,
        args.n_extra,
        args.seed,
        args.skip_downloads,
    )
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
