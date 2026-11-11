"""Backtests. Two ways to score the models on the 2026-07-01 snapshot:

1. Across packages (the main check): 5 folds split by package. The models are fitted on four
   folds and predict the fifth, so every package is predicted by a model that never saw it.
   The target window is the same one the course predictions are scored on.

    python -m model.backtest --test data_full/training_data.sqlite 2026-07-01 \
        --extra cache/snap_2026-04-01/training_data.sqlite 2026-04-01

   --extra adds an earlier snapshot as more training rows ("+apr" models). Its rows get their
   own level (see models.fit_poisson), and rows of the held-out packages are left out.

2. Across time (add --temporal): fit only on the --extra snapshot, score on --test. This
   shows how much the overall advisory level moves from one quarter to the next.

--future-weight sets how much the outcome-selected packages without history count when
fitting: 1 = test packages are picked like our training packages, 0 = picked blind.
Snapshots without downloads borrow them from --test (see data.borrow_downloads).
"""
from __future__ import annotations

import argparse
import math
import random
from datetime import date
from pathlib import Path

from .data import load_packages, read_names
from .features import SEVERITIES
from .models import (fit_lightgbm, fit_poisson, predict_lightgbm, predict_naive,
                     predict_poisson, predict_zero)


def metrics(pairs: list[tuple[float, float]]) -> tuple[float, float, float]:
    """(MAE, RMSE, mean Poisson deviance) over (prediction, truth) pairs."""
    n = len(pairs)
    mae = sum(abs(p - y) for p, y in pairs) / n
    rmse = math.sqrt(sum((p - y) ** 2 for p, y in pairs) / n)
    dev = 0.0
    for p, y in pairs:
        p = max(p, 1e-6)  # a prediction of exactly 0 would give infinite deviance
        dev += 2 * ((y * math.log(y / p) if y > 0 else 0.0) - (y - p))
    return mae, rmse, dev / n


def weights_for(packages: list[dict], future_weight: float) -> list[float]:
    return [future_weight if p["origin"] == "future" else 1.0 for p in packages]


def fit_and_predict(train: list[dict], other_period, test: list[dict], future_weight: float,
                    lightgbm: bool) -> dict[str, list[dict]]:
    """Fit the learned models on train and return their predictions for test."""
    keep = [i for i, p in enumerate(train) if p["origin"] != "future" or future_weight > 0]
    train = [train[i] for i in keep]
    period = [other_period[i] for i in keep] if other_period is not None else None
    weights = weights_for(train, future_weight)
    poisson = fit_poisson(train, weights, other_period=period)
    out = {"poisson": [predict_poisson(poisson, p["features"]) for p in test]}
    if lightgbm:
        out["lightgbm"] = predict_lightgbm(fit_lightgbm(train, weights, period),
                                           [p["features"] for p in test])
    return out


def report(test: list[dict], predictions: dict[str, list[dict]]) -> None:
    subsets = {
        "all packages as collected": lambda p: True,
        "history + blind (blind test mix)": lambda p: p["origin"] != "future",
        "history only": lambda p: p["origin"] == "history",
        "no history as collected": lambda p: p["origin"] != "history",
    }
    for label, keep in subsets.items():
        idx = [i for i, p in enumerate(test) if keep(p)]
        print(f"\n{label} ({len(idx)} packages)")
        print(f"  {'model':13s} {'MAE':>7s} {'RMSE':>7s} {'deviance':>9s}   "
              f"predicted / actual advisories per severity")
        for name, preds in predictions.items():
            pairs = [(preds[i][s], test[i]["targets"][s]) for i in idx for s in SEVERITIES]
            mae, rmse, dev = metrics(pairs)
            totals = "  ".join(
                f"{s[:4]} {sum(preds[i][s] for i in idx):.0f}/"
                f"{sum(test[i]['targets'][s] for i in idx):.0f}" for s in SEVERITIES
            )
            print(f"  {name:13s} {mae:7.4f} {rmse:7.4f} {dev:9.4f}   {totals}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--test", nargs=2, required=True, metavar=("SQLITE", "DATE"))
    parser.add_argument("--extra", nargs=2, metavar=("SQLITE", "DATE"))
    parser.add_argument("--temporal", action="store_true")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--blind-names", type=Path, default=Path("extra_packages_random.txt"))
    parser.add_argument("--future-weight", type=float, default=1.0)
    parser.add_argument("--no-lightgbm", action="store_true")
    args = parser.parse_args()
    if args.temporal and not args.extra:
        parser.error("--temporal needs --extra")

    blind = read_names(args.blind_names)
    test_date = date.fromisoformat(args.test[1])
    test = load_packages(Path(args.test[0]), test_date, blind)
    extra = []
    if args.extra:
        extra_date = date.fromisoformat(args.extra[1])
        shift = (test_date.year - extra_date.year) * 12 + test_date.month - extra_date.month
        extra = load_packages(Path(args.extra[0]), extra_date, blind,
                              downloads_from=(Path(args.test[0]), shift))
    lightgbm = not args.no_lightgbm
    total = lambda pk: sum(sum(p["targets"].values()) for p in pk)  # noqa: E731
    print(f"test {test_date}: {len(test)} packages, {total(test):.0f} advisories; "
          f"extra: {len(extra)} packages, {total(extra):.0f} advisories; "
          f"future-weight {args.future_weight}")

    predictions = {
        "zero": [predict_zero(p["features"]) for p in test],
        "naive 12m": [predict_naive(p["features"]) for p in test],
    }
    if args.temporal:
        print("mode: across time (fit on --extra only)")
        predictions.update(fit_and_predict(extra, None, test, args.future_weight, lightgbm))
    else:
        print(f"mode: across packages ({args.folds} folds)")
        order = list(range(len(test)))
        random.Random(args.seed).shuffle(order)
        fold_of = {test[i]["name"]: n % args.folds for n, i in enumerate(order)}
        for fold in range(args.folds):
            held = [i for i in range(len(test)) if fold_of[test[i]["name"]] == fold]
            held_names = {test[i]["name"] for i in held}
            base = [p for p in test if p["name"] not in held_names]
            more = [p for p in extra if p["name"] not in held_names]
            runs = {"": (base, None)}
            if extra:
                runs["+apr"] = (base + more, [0.0] * len(base) + [1.0] * len(more))
            for suffix, (train, period) in runs.items():
                fitted = fit_and_predict(train, period, [test[i] for i in held],
                                         args.future_weight, lightgbm)
                for name, preds in fitted.items():
                    column = predictions.setdefault(name + suffix, [None] * len(test))
                    for i, pred in zip(held, preds):
                        column[i] = pred
    report(test, predictions)


if __name__ == "__main__":
    main()
