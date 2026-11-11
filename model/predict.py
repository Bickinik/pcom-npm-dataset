"""Predict the four 90-day advisory counts for every package in a records file.

    python -m model.predict records.csv --model model/round1_model.json --out predictions.csv

Input: a CSV (or .sqlite with a training_data table) in the official record schema, one row
per stable version. Target columns are ignored if present.
Output: a CSV with the target columns of the record schema (target_ghsa_<severity>_count_90d),
one record per package in the input, so one record for the usual one-package file. The counts
for low, moderate, high and critical are floats; unknown is always 0 (not modelled).
--package-column adds the package name as the first column.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from .data import read_records
from .features import PREDICTION_DATE, SEVERITIES, group_by_package, package_features
from .models import load_model, predict_lightgbm, predict_poisson


def predict_packages(model: dict, groups: dict[str, list[dict]],
                     prediction_date: date = PREDICTION_DATE) -> dict[str, dict[str, float]]:
    """{package: its records} -> {package: {"low": .., "moderate": .., "high": .., "critical": ..}}."""
    names = sorted(groups)
    features = [package_features(groups[name], prediction_date) for name in names]
    if model["kind"] == "lightgbm":
        predictions = predict_lightgbm(model, features)
    else:
        predictions = [predict_poisson(model, f) for f in features]
    return dict(zip(names, predictions))


def read_input(path: Path) -> list[dict]:
    if path.suffix in (".sqlite", ".db"):
        return read_records(path)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", type=Path)
    parser.add_argument("--model", type=Path, default=Path("model/round1_model.json"))
    parser.add_argument("--prediction-date", type=date.fromisoformat, default=PREDICTION_DATE)
    parser.add_argument("--out", type=Path, help="CSV file (default: standard output)")
    parser.add_argument("--package-column", action="store_true",
                        help="add the package name as the first column")
    args = parser.parse_args()

    model = load_model(args.model)
    groups = group_by_package(read_input(args.records))
    out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    try:
        writer = csv.writer(out)
        targets = [f"target_ghsa_{sev}_count_90d" for sev in SEVERITIES + ["unknown"]]
        writer.writerow((["package"] if args.package_column else []) + targets)
        for name, pred in predict_packages(model, groups, args.prediction_date).items():
            counts = [f"{pred[sev]:.4f}" for sev in SEVERITIES] + ["0"]
            writer.writerow(([name] if args.package_column else []) + counts)
    finally:
        if args.out:
            out.close()


if __name__ == "__main__":
    main()
