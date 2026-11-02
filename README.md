# npm vulnerability prediction - training dataset (HW3)

Builds the training dataset for predicting the number of new GitHub-reviewed security advisories
(GHSA) per severity in the 90 days after 1 July 2026, for npm packages. The record schema is
`schema/PCOM-01_Record_Schema_Public.xlsx` (224 columns once M0..M11 are expanded);
`schema/training_data.sql` is the `CREATE TABLE` generated from it.

Standard library only for the collector. Tests use `unittest` (and `openpyxl` for the Excel check,
skipped if missing).

## Decisions and assumptions (not yet confirmed with the instructor)

| Topic | Decision |
|---|---|
| Prediction date | 2026-07-01 00:00 UTC. M0 = June 2026 ... M11 = July 2025; months are end-exclusive. |
| Target window | 2026-07-01 up to (not including) 2026-09-29, i.e. 90 days. This window is already over, so targets are observed. |
| Rows | One row per **stable** version (no prerelease) first published between 2025-07-01 and 2026-06-30. A package with no such version gets no rows. |
| Package-level columns | Downloads, release counts, GHSA counts and targets belong to the package and repeat on every row of that package. |
| "Qualifying" GHSA | GitHub-reviewed, npm ecosystem, not withdrawn; distinct GHSA ids per package (an advisory listing several npm packages counts for each). |
| Severity | Taken from the advisory-database snapshot at build time, for both the history counts ("at prediction") and the targets ("at window end"). Missing or unrecognised severity -> `unknown`. |
| Advisory date | The advisory's `published` field. |
| Downloads | Sum of the daily observations available per month; no extrapolation. A month with no observed day has downloads NULL and days_observed 0; a failed request gives all NULL. |
| Bundled dependencies | `bundleDependencies: true` counts all `dependencies`; a list counts its distinct names. |
| GitHub issue / PR columns | Left NULL in the first version (optional in the schema). Can be added later without changing the schema. |
| purl | `pkg:npm/<name>@<version>`; the `@` of a scope is percent-encoded (`pkg:npm/%40babel/core@7.0.0`). |

## Run

```bash
# 1. advisories (bulk clone, no API limits)
git clone --depth 1 https://github.com/github/advisory-database ../advisory-database

# 2. tests (offline)
python3 -m unittest discover -s tests -t .

# 3. smoke test on a few packages -> check the output by hand first
python3 -m npmdata.build_dataset --advisory-db ../advisory-database --max-packages 20 --out data_smoke

# 4. full run (resumable: raw responses are cached in cache/)
python3 -m npmdata.build_dataset --advisory-db ../advisory-database \
    --extra-packages extra_packages.txt --workers 4
```

Output in `data/`: `training_data.sqlite`, `training_data.csv`, `build_info.json`,
`errors.tsv` (packages to retry; delete nothing from `cache/`, just rerun).

### Which packages

All npm packages that appear in at least one GHSA, plus the packages listed in
`extra_packages.txt` (one name per line, `#` for comments). Without the extra sample almost every
target would be 0, so add packages without advisories (e.g. the most-downloaded ones and a random
sample) and describe how you chose them here:

> Sampling used for `initial-training-data`: _fill in_

The share of packages with advisories is higher than in all of npm, so base rates in this dataset
are not the base rates of npm.

## Before the full run: check one package by hand

`npmdata/sources.py` is the only network code and was not run against the live services when it
was written. Check one package (for example `minimist`) against the browser:
`https://registry.npmjs.org/minimist`,
`https://api.npmjs.org/downloads/range/2025-07-01:2026-06-30/minimist`, and the matching
advisories on osv.dev. Scoped packages (`@scope/name`) are the most likely to need a fix in the URL
handling, so include one in the smoke test.

## Submit

```bash
git add -A && git commit -m "Initial training data"
git tag initial-training-data
git push && git push origin initial-training-data   # tags are not pushed by default
```

Check file sizes before pushing (GitHub rejects files over 100 MB); compress the CSV if needed.

## Layout

```
npmdata/schema.py        column list + DDL from the record schema
npmdata/advisories.py    advisory-database -> GHSA counts and targets
npmdata/registry.py      registry document -> version rows
npmdata/downloads.py     daily downloads -> monthly sums
npmdata/sources.py       HTTP + cache (network)
npmdata/build_dataset.py orchestration, SQLite + CSV output
tests/                   offline tests (fixture: real minimist registry document)
```
