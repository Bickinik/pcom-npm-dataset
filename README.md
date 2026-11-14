# npm vulnerability prediction - training data and model

Builds the training dataset for predicting the number of new GitHub-reviewed security advisories
(GHSA) per severity in the 90 days after 1 July 2026, for npm packages.
The record schema is `schema/PCOM-01_Record_Schema_Public.xlsx` (course material, not in this
repo; 224 columns once M0..M11 are expanded). `schema/training_data.sql` is the `CREATE TABLE`
generated from it.

Standard library only for the collector. Tests use `unittest` (and `openpyxl` for the Excel check,
skipped if missing).

## Datasets

| Folder | What | Rows | Packages | Downloads |
|---|---|---|---|---|
| `data/` | HW3 submission, tag `initial-training-data` | 4,094 | 134 | NULL (built with `--skip-downloads`) |
| `data_full/` | Full run of 2026-11-11, rebuilt 2026-11-14 with GitHub columns | 115,472 | 7,084 | filled |

Each folder has `training_data.sqlite`, `training_data.csv`, `build_info.json` and `errors.tsv`
(packages that failed).

In `data_full/`, the GitHub issue / pull request columns are filled for 4,401 of the 7,084
packages (62%). They are NULL when the package names no GitHub repository, the repository is gone
or blocked, or it is too large to fetch (one case). The two `*_reopened` series are NULL for all.

## Run

```bash
# 1. advisories (bulk clone, no API limits)
git clone --depth 1 https://github.com/github/advisory-database ../advisory-database

# 2. tests (offline)
python3 -m unittest discover -s tests -t .

# 3. smoke test on a few packages -> check the output by hand first
python3 -m npmdata.build_dataset --advisory-db ../advisory-database --max-packages 20 --out data_smoke

# 4a. HW3 dataset -> data/
python3 -m npmdata.build_dataset --advisory-db ../advisory-database \
    --extra-packages extra_packages.txt --n-advisory 250 --n-extra 250 --seed 42 \
    --workers 4 --skip-downloads

# 4b. full run -> data_full/ (resumable: raw responses are cached in cache/)
python3 -m npmdata.build_dataset --advisory-db ../advisory-database \
    --extra-packages extra_packages_random.txt --seed 42 --workers 4 --out data_full

# 4c. GitHub issue / PR columns: fetch once per repository into cache/github/ (several hours;
#     set GITHUB_TOKEN for 5,000 requests per hour; stop and rerun at any time), then rebuild
python3 -m npmdata.fetch_github data_full/training_data.sqlite
python3 -m npmdata.build_dataset --advisory-db ../advisory-database \
    --extra-packages extra_packages_random.txt --seed 42 --workers 4 --out data_full --github
```

On Windows, `run_full.ps1` runs the steps of the full run. If packages end up in `errors.tsv`,
rerun the same command; delete nothing from `cache/`.

### Which packages

Packages that appear in at least one GHSA, plus packages from a list without regard to advisories
(one name per line, `#` for comments). Without the second group almost every target would be 0.
Only packages with a stable release in the 12-month window produce rows.

- **`data/` (HW3):** 250 advisory packages and 250 names from `extra_packages.txt` (taken from
  npm search results), both drawn at random with seed 42. 134 of the 500 produced rows.
- **`data_full/`:** all 3,723 advisory packages, plus `extra_packages_random.txt`: 30,000 names
  drawn uniformly (seed 42) from all 4,657,015 names in `all-the-package-names` 2.0.2619
  (2026-11-09), made with `python -m npmdata.sample_names <tarball> --n 30000 --seed 42`.
  1,707 of the advisory packages and 5,377 of the 29,975 random packages (18%) produced rows.
  Only 302 packages (4.3%) have an advisory in the target window.

The share of packages with advisories is higher than in all of npm, so base rates in these
datasets are not the base rates of npm.

## Model (prediction rounds)

`model/` turns the records of a package into predicted counts of new advisories per severity.
It needs the packages in `requirements-model.txt`; the collector stays standard library only.

```bash
# fit and predict
python -m model.train --snapshot data_full/training_data.sqlite 2026-07-01 \
    --out model/round1_model.json
python -m model.predict records.csv --model model/round1_model.json --out predictions.csv

# backtest: 5 folds split by package
python -m model.backtest --test data_full/training_data.sqlite 2026-07-01

# optional: an earlier snapshot as extra training rows (registry cache is reused, no downloads);
# pass it to the backtest with --extra and to train with a second --snapshot
python -m npmdata.build_dataset --advisory-db ../advisory-database \
    --extra-packages extra_packages_random.txt --skip-downloads \
    --prediction-date 2026-04-01 --out cache/snap_2026-04-01
```

`model.predict` reads a CSV in the record schema and writes a CSV with the schema's target
columns (`target_ghsa_<severity>_count_90d`), one record per package in the input. The counts
are floats; `unknown` is always 0 because it is not modelled.

Round 1 model (`model/round1_model.json`): LightGBM with a Poisson loss, one model per severity,
on package-level features built from the schema columns only (`model/features.py`): release
activity, size and dependency counts, downloads, advisory history, and GitHub issue and pull
request activity. Columns that are empty in the records are treated as missing values.
`model/round1_model_no_github.json` is an earlier version without the GitHub columns.

## Prediction service (automated rounds)

From round 2 on, the competition software calls a service on a VM
(protocol: [pcom-course-setup](https://github.com/riehlegroup/pcom-course-setup)).
`model/service.py` wraps the predictor: `POST /predict` gets a download URL for the test records
and an upload URL for the prediction, `GET /health` answers `{"health":"up"}`. If a round fails
for any reason, the service uploads all zeros instead of nothing.

```bash
# on the VM, with Docker
docker build -t pcom-predict .
docker run -d --name pcom-predict --restart unless-stopped -p 8000:8000 pcom-predict

# or without Docker
pip install -r requirements-service.txt
uvicorn model.service:app --host 0.0.0.0 --port 8000

curl http://localhost:8000/health
docker logs pcom-predict        # predictions and the evaluation result of each round
```

## Layout

```
npmdata/schema.py        column list + DDL from the record schema
npmdata/advisories.py    advisory-database -> GHSA counts and targets
npmdata/registry.py      registry document -> version rows
npmdata/downloads.py     daily downloads -> monthly sums
npmdata/sources.py       HTTP + cache (network)
npmdata/github.py        issues and pull requests of a repository -> monthly columns
npmdata/fetch_github.py  fetch issues and pull requests for the packages of a dataset
npmdata/build_dataset.py orchestration, SQLite + CSV output
npmdata/sample_names.py  random package names for the full run
model/features.py        records of one package -> feature vector
model/data.py            load snapshots, mark outcome-selected packages
model/models.py          baselines, Poisson regression, LightGBM
model/backtest.py        5-fold backtest split by package
model/train.py           fit the model -> JSON file
model/service.py         web service for the automated rounds (/health, /predict)
model/predict.py         records -> predicted counts per package
tests/                   offline tests (fixture: real minimist registry document)
```
