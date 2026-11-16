"""Predict the four 90-day advisory counts for every package in a records file.

    python -m model.predict records.csv --model model/round1_model.json --out predictions.csv

Input: a CSV (or .sqlite with a training_data table) in the official record schema, one row
per stable version. Target columns are ignored if present. The purl column may be missing:
the records are then grouped by package_created_at, the newest release counts as the latest
version, and the major version is a missing value.
Output: a CSV with the target columns of the record schema (target_ghsa_<severity>_count_90d),
one record per package in the input, so one record for the usual one-package file. The counts
for low, moderate, high and critical are floats; unknown is always 0 (not modelled).
--package-column adds the package name as the first column.

--model predicts every package; it uses the GitHub issue / PR columns where they are filled.
Optional: with --github-model, that model predicts the packages whose GitHub columns are
filled and --model the others (for a --model that was fitted without those columns).
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from .data import read_records
from .features import (GITHUB_PREFIX, PREDICTION_DATE, SEVERITIES, group_by_package,
                       package_features)
from .models import load_model, predict_lightgbm, predict_poisson

GITHUB_MODEL = Path("model/round1_github_model.json")


def has_github(features: dict) -> bool:
    """True when the records have at least one issue / PR column filled."""
    return any(v is not None for k, v in features.items() if k.startswith(GITHUB_PREFIX))


def _predict(model: dict, features: list[dict]) -> list[dict[str, float]]:
    if model["kind"] == "lightgbm":
        return predict_lightgbm(model, features)
    return [predict_poisson(model, f) for f in features]


def predict_packages(model: dict, groups: dict[str, list[dict]],
                     prediction_date: date = PREDICTION_DATE,
                     github_model: dict | None = None) -> dict[str, dict[str, float]]:
    """{package: its records} -> {package: {"low": .., "moderate": .., "high": .., "critical": ..}}.

    github_model, if given, predicts the packages whose issue / PR columns are filled; `model`
    predicts the others (it must not need those columns).
    """
    names = sorted(groups)
    features = [package_features(groups[name], prediction_date) for name in names]
    with_github = [github_model is not None and has_github(f) for f in features]
    predictions: list = [None] * len(names)
    for chosen, flag in ((github_model, True), (model, False)):
        idx = [i for i, g in enumerate(with_github) if g == flag]
        if idx:
            for i, pred in zip(idx, _predict(chosen, [features[i] for i in idx])):
                predictions[i] = pred
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
    parser.add_argument("--github-model", type=Path, default=GITHUB_MODEL,
                        help="model for packages whose issue / PR columns are filled "
                             "(skipped if the file does not exist)")
    parser.add_argument("--no-github-model", action="store_true",
                        help="use --model for every package")
    parser.add_argument("--prediction-date", type=date.fromisoformat, default=PREDICTION_DATE)
    parser.add_argument("--out", type=Path, help="CSV file (default: standard output)")
    parser.add_argument("--package-column", action="store_true",
                        help="add the package name as the first column")
    args = parser.parse_args()

    model = load_model(args.model)
    github_model = None
    if not args.no_github_model and args.github_model.exists():
        github_model = load_model(args.github_model)
    groups = group_by_package(read_input(args.records))
    out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    try:
        writer = csv.writer(out)
        targets = [f"target_ghsa_{sev}_count_90d" for sev in SEVERITIES + ["unknown"]]
        writer.writerow((["package"] if args.package_column else []) + targets)
        predictions = predict_packages(model, groups, args.prediction_date, github_model)
        for name, pred in predictions.items():
            counts = [f"{pred[sev]:.4f}" for sev in SEVERITIES] + ["0"]
            writer.writerow(([name] if args.package_column else []) + counts)
    finally:
        if args.out:
            out.close()


if __name__ == "__main__":
    main()
