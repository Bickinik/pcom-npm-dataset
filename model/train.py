"""Fit the prediction model on one or more snapshots and store it as one JSON file.

    python -m model.train --snapshot cache/snap_2026-04-01/training_data.sqlite 2026-04-01 \
        --snapshot data_full/training_data.sqlite 2026-07-01 --out model/round1_model.json

The latest snapshot sets the overall level; earlier ones get their own level, which is
dropped (see models.fit_poisson). They also borrow their downloads from the latest one
(see data.borrow_downloads).

--exclude-records FILE leaves out every package that appears in FILE (the records to predict),
so the model never sees the actual outcome of a package it has to predict.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .backtest import weights_for
from .data import load_packages, read_names
from .features import group_by_package
from .models import fit_lightgbm, fit_poisson, save_model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", nargs=2, action="append", required=True,
                        metavar=("SQLITE", "DATE"))
    parser.add_argument("--blind-names", type=Path, default=Path("extra_packages_random.txt"))
    parser.add_argument("--future-weight", type=float, default=1.0)
    parser.add_argument("--kind", choices=["lightgbm", "poisson"], default="lightgbm")
    parser.add_argument("--exclude-records", type=Path,
                        help="records file to predict; its packages are left out of training")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    blind = read_names(args.blind_names)
    excluded: set[str] = set()
    if args.exclude_records:
        from .predict import read_input
        excluded = set(group_by_package(read_input(args.exclude_records)))
    snapshots = sorted((date.fromisoformat(d), Path(p)) for p, d in args.snapshot)
    last_date, last_path = snapshots[-1]
    packages, period = [], []
    for snap_date, path in snapshots:
        shift = (last_date.year - snap_date.year) * 12 + last_date.month - snap_date.month
        donor = (last_path, shift) if shift else None
        loaded = load_packages(path, snap_date, blind, downloads_from=donor)
        kept = [p for p in loaded if p["name"] not in excluded
                and (p["origin"] != "future" or args.future_weight > 0)]
        packages += kept
        period += [1.0 if shift else 0.0] * len(kept)
        print(f"{snap_date}: {len(kept)} packages ({len(loaded) - len(kept)} left out)")
    fit = fit_lightgbm if args.kind == "lightgbm" else fit_poisson
    model = fit(packages, weights_for(packages, args.future_weight),
                other_period=period if any(period) else None)
    model["trained_on"] = [d.isoformat() for d, _ in snapshots]
    model["future_weight"] = args.future_weight
    save_model(model, args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
