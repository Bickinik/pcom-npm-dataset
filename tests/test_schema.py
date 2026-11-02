import sqlite3
import unittest
from datetime import date
from pathlib import Path

from npmdata import schema

XLSX = Path(__file__).resolve().parent.parent / "schema" / "PCOM-01_Record_Schema_Public.xlsx"


def sheet_field_names():
    import openpyxl

    ws = openpyxl.load_workbook(XLSX)["Schema"]
    names = []
    for row in range(1, ws.max_row + 1):
        value = ws.cell(row=row, column=3).value
        if value and value != "…" and value != "Field name":
            names.append(value)
    return names


class SchemaTests(unittest.TestCase):
    def test_column_count_and_uniqueness(self):
        names = schema.column_names()
        self.assertEqual(len(names), 224)
        self.assertEqual(len(set(names)), 224)

    def test_matches_excel_sheet(self):
        try:
            sheet = sheet_field_names()
        except ImportError:
            self.skipTest("openpyxl not installed")
        generated = schema.column_names()
        self.assertEqual(len(sheet), 54)  # sheet lists M0 and M11 of each series only
        # every sheet field exists, and appears in the same relative order
        positions = [generated.index(name) for name in sheet]
        self.assertEqual(positions, sorted(positions))

    def test_ddl_runs_and_enforces_constraints(self):
        con = sqlite3.connect(":memory:")
        con.executescript(schema.ddl(date(2026, 7, 1)))
        self.assertEqual(len(con.execute(f"PRAGMA table_info({schema.TABLE})").fetchall()), 224)

        names = schema.column_names()
        required = {c.name for c in schema.columns() if c.required}

        def row(**overrides):
            base = {n: (0 if n in required else None) for n in names}
            base.update(purl="pkg:npm/foo@1.0.0",
                        package_created_at="2020-01-01T00:00:00.000Z",
                        published_at="2026-01-01T00:00:00.000Z")
            base.update(overrides)
            return base

        sql = (f"INSERT INTO {schema.TABLE} ({', '.join(names)}) "
               f"VALUES ({', '.join(':' + n for n in names)})")
        con.execute(sql, row())
        for bad in (
            row(purl="pkg:npm/foo@1.0.0"),  # duplicate purl
            row(purl="pkg:pypi/foo@1.0.0"),  # not npm
            row(purl="pkg:npm/bar@1.0.0", published_at="2026-01-01T00:00:00Z"),  # no millis
            row(purl="pkg:npm/bar@1.0.0", dependency_count=-1),
            # M1 = May 2026 has 31 days, M2 = April 2026 has 30
            row(purl="pkg:npm/bar@1.0.0", package_download_days_observed_M2=31),
        ):
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(sql, bad)
        con.execute(sql, row(purl="pkg:npm/baz@1.0.0", package_download_days_observed_M1=31))
        con.close()


if __name__ == "__main__":
    unittest.main()
