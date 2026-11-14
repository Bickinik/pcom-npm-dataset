"""Prediction service for the automated competition rounds.

    uvicorn model.service:app --host 0.0.0.0 --port 8000

The competition software calls POST /predict with two URLs. The service answers at once,
then downloads the test records (CSV, one package), predicts, and uploads a CSV with one
record of target columns. GET /health tells the caller that the service is up.
Protocol and field names follow https://github.com/riehlegroup/pcom-course-setup.

If anything goes wrong (download, unreadable records, model error), all zeros are uploaded:
a prediction of no new advisories is a fair default, and better than no upload.

Environment: PCOM_MODEL = path of the model file (default: model/round1_model.json),
PCOM_GITHUB_MODEL = model for records with GitHub issue / PR columns (default:
model/round1_github_model.json, skipped if missing). See model/predict.py.
"""
from __future__ import annotations

import csv
import io
import logging
import math
import os
from pathlib import Path

import lightgbm  # noqa: F401  (imported here so the first request does not pay for it)
import requests
from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel

from .features import SEVERITIES, group_by_package
from .models import load_model
from .predict import predict_packages

log = logging.getLogger("service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

TARGETS = [f"target_ghsa_{sev}_count_90d" for sev in SEVERITIES + ["unknown"]]
MODEL_PATH = Path(os.environ.get("PCOM_MODEL")
                  or Path(__file__).resolve().parent / "round1_model.json")
MODEL = load_model(MODEL_PATH)
GITHUB_MODEL_PATH = Path(os.environ.get("PCOM_GITHUB_MODEL")
                         or Path(__file__).resolve().parent / "round1_github_model.json")
GITHUB_MODEL = load_model(GITHUB_MODEL_PATH) if GITHUB_MODEL_PATH.exists() else None
TIMEOUT = 30

app = FastAPI(title="PCOM npm advisory prediction")


class EvaluationBody(BaseModel):
    testdata_download_endpoint: str
    prediction_upload_endpoint: str


def predict_counts(records_csv: str) -> dict[str, float]:
    """Records of one package as CSV text -> {target column: count}."""
    groups = group_by_package(list(csv.DictReader(io.StringIO(records_csv))))
    if not groups:
        raise ValueError("no records in the input")
    name = max(groups, key=lambda n: len(groups[n]))
    if len(groups) > 1:  # one record is expected back, so only one package can be answered
        log.warning("%d packages in the input, predicting %s", len(groups), name)
    pred = predict_packages(MODEL, {name: groups[name]}, github_model=GITHUB_MODEL)[name]
    counts = {f"target_ghsa_{sev}_count_90d": pred[sev] for sev in SEVERITIES}
    if not all(math.isfinite(v) and v >= 0 for v in counts.values()):
        raise ValueError(f"unusable prediction for {name}: {counts}")
    log.info("%s (%d records): %s", name, len(groups[name]),
             " ".join(f"{sev}={pred[sev]:.4f}" for sev in SEVERITIES))
    return counts


def to_csv(counts: dict[str, float]) -> str:
    """One header line and one record; columns that are not predicted (unknown) are 0."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(TARGETS)
    writer.writerow([f"{counts[t]:.4f}" if t in counts else "0" for t in TARGETS])
    return out.getvalue()


def run_round(download_url: str, upload_url: str) -> None:
    counts: dict[str, float] = {}
    try:
        response = requests.get(download_url, timeout=TIMEOUT)
        response.raise_for_status()
        counts = predict_counts(response.content.decode("utf-8-sig"))
    except Exception:
        log.exception("prediction failed, uploading zeros")
    try:
        files = {"predictions": ("prediction.csv", to_csv(counts), "text/csv")}
        response = requests.post(upload_url, files=files, timeout=TIMEOUT)
        log.info("upload: HTTP %d %s", response.status_code, response.text[:2000])
    except Exception:
        log.exception("upload failed")


@app.post("/predict", status_code=200)
def predict(payload: EvaluationBody, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_round, payload.testdata_download_endpoint,
                              payload.prediction_upload_endpoint)
    return {"status": "prediction started"}


@app.get("/health", status_code=200)
def health():
    return {"health": "up"}
