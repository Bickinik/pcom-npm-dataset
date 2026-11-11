"""Load a built dataset (training_data.sqlite) as one (features, targets) pair per package."""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from npmdata.timeutil import N_MONTHS

from .features import group_by_package, package_features, package_targets, split_purl


def read_records(sqlite_path: Path) -> list[dict]:
    con = sqlite3.connect(sqlite_path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute("SELECT * FROM training_data")]
    finally:
        con.close()


def read_names(path: Path) -> set[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


def borrow_downloads(records: list[dict], donor_records: list[dict], shift: int) -> None:
    """Fill the download columns of a snapshot built with --skip-downloads, in place.

    The donor is a later snapshot of the same packages whose prediction date is `shift` months
    later, so its month M(i+shift) is this snapshot's month Mi. Months the donor does not cover
    (the oldest `shift` ones) and packages missing from the donor stay NULL.
    """
    donors = {name: recs[0] for name, recs in group_by_package(donor_records).items()}
    for record in records:
        donor = donors.get(split_purl(record["purl"])[0])
        for i in range(N_MONTHS):
            for metric in ("package_downloads", "package_download_days_observed"):
                j = i + shift
                record[f"{metric}_M{i}"] = donor.get(f"{metric}_M{j}") if donor and j < N_MONTHS else None


def load_packages(sqlite_path: Path, prediction_date: date, blind_names: set[str],
                  downloads_from: tuple[Path, int] | None = None) -> list[dict]:
    """One dict per package: name, features, targets and how the package got into the data.

    origin = "history": has an advisory before the prediction date (all such packages are
                        collected, and the predictor can recognise them the same way).
             "blind":   no history, drawn in the random sample without looking at outcomes.
             "future":  no history, collected only because today's advisory database lists it,
                        i.e. selected by an advisory on or after the prediction date.
    """
    all_records = read_records(sqlite_path)
    if downloads_from:
        borrow_downloads(all_records, read_records(downloads_from[0]), downloads_from[1])
    out = []
    for name, records in sorted(group_by_package(all_records).items()):
        features = package_features(records, prediction_date)
        if features["ghsa_total"] > 0:
            origin = "history"
        elif name in blind_names:
            origin = "blind"
        else:
            origin = "future"
        out.append({"name": name, "origin": origin, "features": features,
                    "targets": package_targets(records)})
    return out
