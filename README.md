# npm vulnerability prediction - training dataset (HW3)

Builds the training dataset for predicting the number of new GitHub-reviewed security advisories
(GHSA) per severity in the 90 days after 1 July 2026, for npm packages. The record schema is
`schema/training_data.sql` is the `CREATE TABLE` generated from it.

Standard library only for the collector. Tests use `unittest` (and `openpyxl` for the Excel check,
skipped if missing).

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
target would be 0

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
