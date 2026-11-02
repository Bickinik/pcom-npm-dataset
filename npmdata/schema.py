"""Record schema (PCOM-01) and the SQLite DDL generated from it.

Column names and order follow schema/PCOM-01_Record_Schema_Public.xlsx, with the
"..." rows expanded to M0..M11. tests/test_schema.py checks this against the Excel file.

Run `python -m npmdata.schema` to print the DDL (default prediction date 2026-07-01).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date

from .timeutil import N_MONTHS, month_ranges

TABLE = "training_data"
SEVERITIES = ["low", "moderate", "high", "critical", "unknown"]

TS_GLOB = (
    "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T"
    "[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'"
)


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str  # TEXT or INTEGER
    required: bool
    kind: str = "count"  # purl | timestamp | count | days_observed
    month: int | None = None


def _series(metric: str, required: bool, kind: str = "count") -> list[Column]:
    return [
        Column(f"{metric}_M{i}", "INTEGER", required, kind, i) for i in range(N_MONTHS)
    ]


def columns() -> list[Column]:
    cols = [
        Column("purl", "TEXT", True, "purl"),
        Column("package_created_at", "TEXT", True, "timestamp"),
        Column("published_at", "TEXT", True, "timestamp"),
        Column("unpacked_size", "INTEGER", False),
        Column("file_count", "INTEGER", False),
        Column("dependency_count", "INTEGER", True),
        Column("optional_dependency_count", "INTEGER", True),
        Column("peer_dependency_count", "INTEGER", True),
        Column("dev_dependency_count", "INTEGER", True),
        Column("bundled_dependency_count", "INTEGER", True),
    ]
    cols += _series("package_downloads", False)
    cols += _series("package_download_days_observed", False, "days_observed")
    cols += _series("package_stable_release_count", False)
    for metric in [
        "issues_opened",
        "issues_closed",
        "issues_reopened",
        "pull_requests_opened",
        "pull_requests_closed_unmerged",
        "pull_requests_merged",
        "pull_requests_reopened",
        "issues_open_at_month_end",
        "pull_requests_open_at_month_end",
    ]:
        cols += _series(metric, False)
    for sev in SEVERITIES:
        cols.append(Column(f"ghsa_{sev}_count", "INTEGER", True))
    for sev in SEVERITIES:
        cols += _series(f"ghsa_{sev}_count", True)
    for sev in SEVERITIES:
        cols.append(Column(f"target_ghsa_{sev}_count_90d", "INTEGER", True))
    return cols


def column_names() -> list[str]:
    return [c.name for c in columns()]


def ddl(prediction_date: date) -> str:
    """CREATE TABLE statement, one column per line, with the sheet's constraints."""
    months = month_ranges(prediction_date)
    lines = []
    for c in columns():
        parts = [c.name, c.sql_type]
        if c.required:
            parts.append("NOT NULL")
        if c.kind == "purl":
            parts.append("UNIQUE")
            parts.append("CHECK (purl LIKE 'pkg:npm/%' AND length(purl) <= 2048)")
        elif c.kind == "timestamp":
            parts.append(f"CHECK ({c.name} GLOB {TS_GLOB})")
        elif c.kind == "days_observed":
            start, end = months[c.month]
            parts.append(
                f"CHECK ({c.name} IS NULL OR {c.name} BETWEEN 0 AND {(end - start).days})"
            )
        else:
            parts.append(f"CHECK ({c.name} IS NULL OR {c.name} >= 0)")
        lines.append("    " + " ".join(parts))
    return f"CREATE TABLE {TABLE} (\n" + ",\n".join(lines) + "\n);\n"


if __name__ == "__main__":
    pred = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 7, 1)
    sys.stdout.write(ddl(pred))
