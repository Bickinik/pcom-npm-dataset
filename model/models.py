"""Predictors: package features -> expected advisory counts per severity (floats).

- zero:      always 0 (reference point).
- naive:     advisories of that severity in the last 12 months x 90/365 (the HW4 baseline).
- poisson:   a small Poisson regression per severity on log-scaled features. Fitted with
             scikit-learn, stored as plain coefficients, predicted with the standard library.
- lightgbm:  gradient-boosted trees with a Poisson loss, one model per severity, on all
             numeric features. The model used for predictions (see backtest.py).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .features import GITHUB_PREFIX, SEVERITIES

HORIZON = 90 / 365


def predict_zero(features: dict) -> dict[str, float]:
    return {sev: 0.0 for sev in SEVERITIES}


def predict_naive(features: dict) -> dict[str, float]:
    return {sev: features[f"ghsa_{sev}_12m"] * HORIZON for sev in SEVERITIES}


# --- Poisson regression -----------------------------------------------------------------

FEATURE_NAMES = [
    "has_history",        # any advisory before the prediction date
    "log_sev_12m",        # this severity, last 12 months
    "log_sev_3m",         # this severity, last 3 months
    "log_other_12m",      # other severities, last 12 months
    "log_older",          # all severities, before the 12-month window
    "log_downloads_1m",   # downloads in M0 (0 when missing)
    "downloads_missing",
    "log_releases_12m",
]


def feature_vector(features: dict, sev: str) -> list[float]:
    sev_12m = features[f"ghsa_{sev}_12m"]
    downloads = features.get("downloads_1m")
    return [
        1.0 if features["ghsa_total"] > 0 else 0.0,
        math.log1p(sev_12m),
        math.log1p(features[f"ghsa_{sev}_3m"]),
        math.log1p(features["ghsa_12m"] - sev_12m),
        math.log1p(features["ghsa_total"] - features["ghsa_12m"]),
        math.log10(1 + downloads) if downloads is not None else 0.0,
        1.0 if downloads is None else 0.0,
        math.log1p(features.get("releases_12m") or 0.0),
    ]


def fit_poisson(packages: list[dict], weights: list[float], alpha: float = 1e-3,
                other_period: list[float] | None = None) -> dict:
    """One model per severity. Returns {"features": [...], "coef": {sev: [intercept, w...]}}.

    other_period marks rows (1.0) from a snapshot with a different target window. They get
    their own level, which is dropped afterwards: such rows teach the model how features
    relate to advisories, but not how many advisories the predicted window has overall.
    """
    import numpy as np
    from sklearn.linear_model import PoissonRegressor

    model = {"kind": "poisson", "features": FEATURE_NAMES, "coef": {}}
    w = np.array(weights)
    for sev in SEVERITIES:
        x = np.array([feature_vector(p["features"], sev) for p in packages])
        if other_period is not None:
            x = np.column_stack([x, other_period])
        y = np.array([p["targets"][sev] for p in packages])
        reg = PoissonRegressor(alpha=alpha, max_iter=5000).fit(x, y, sample_weight=w)
        coef = [float(c) for c in reg.coef_][:len(FEATURE_NAMES)]
        model["coef"][sev] = [float(reg.intercept_)] + coef
    return model


def predict_poisson(model: dict, features: dict) -> dict[str, float]:
    if model["features"] != FEATURE_NAMES:
        raise ValueError("model was fitted with a different feature list")
    out = {}
    for sev in SEVERITIES:
        coef = model["coef"][sev]
        z = coef[0] + sum(c * x for c, x in zip(coef[1:], feature_vector(features, sev)))
        out[sev] = math.exp(min(z, 6.0))  # cap at about 400 advisories per severity
    return out


# --- LightGBM ---------------------------------------------------------------------------

# downloads_12m is left out: snapshots with borrowed downloads only cover 9 of the 12 months.
LIGHTGBM_EXCLUDE = {"downloads_12m"}


def _matrix(model_features: list[str], feature_dicts: list[dict], period: list[float]):
    import numpy as np

    rows = [[np.nan if f.get(k) is None else f[k] for k in model_features] for f in feature_dicts]
    return np.column_stack([np.array(rows, dtype=float), period])


def fit_lightgbm(packages: list[dict], weights: list[float],
                 other_period: list[float] | None = None, github: bool = True) -> dict:
    """One Poisson model per severity. other_period: see fit_poisson (0 when predicting).

    github=False leaves out the issue / PR features (the model for records without them).
    """
    import lightgbm as lgb

    names = sorted(k for k in packages[0]["features"] if k not in LIGHTGBM_EXCLUDE
                   and (github or not k.startswith(GITHUB_PREFIX)))
    period = other_period if other_period is not None else [0.0] * len(packages)
    x = _matrix(names, [p["features"] for p in packages], period)
    model = {"kind": "lightgbm", "features": names, "boosters": {}}
    for sev in SEVERITIES:
        reg = lgb.LGBMRegressor(
            objective="poisson", n_estimators=300, learning_rate=0.03, num_leaves=8,
            min_child_samples=20, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            random_state=42, verbose=-1,
        ).fit(x, [p["targets"][sev] for p in packages], sample_weight=weights)
        model["boosters"][sev] = reg.booster_.model_to_string()
    return model


def predict_lightgbm(model: dict, feature_dicts: list[dict]) -> list[dict[str, float]]:
    import lightgbm as lgb

    x = _matrix(model["features"], feature_dicts, [0.0] * len(feature_dicts))
    cols = {sev: lgb.Booster(model_str=model["boosters"][sev]).predict(x) for sev in SEVERITIES}
    return [{sev: float(cols[sev][i]) for sev in SEVERITIES} for i in range(len(feature_dicts))]


def save_model(model: dict, path: Path) -> None:
    Path(path).write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")


def load_model(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
