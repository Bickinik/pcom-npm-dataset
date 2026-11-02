import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from npmdata import advisories, build_dataset, downloads, registry, schema
from npmdata.timeutil import format_ts, month_ranges, parse_ts

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PRED = date(2024, 1, 1)  # the minimist fixture ends in 2023, so test with a 2023 window
MONTHS = month_ranges(PRED)


def minimist_doc():
    return json.loads((FIXTURES / "registry_minimist.json").read_text())


def scoped_doc():
    return {
        "name": "@scope/other",
        "time": {
            "created": "2023-03-01T00:00:00.000Z",
            "1.0.0": "2023-03-01T00:00:00.000Z",
            "1.1.0-beta.1": "2023-04-01T00:00:00.000Z",
            "1.1.0": "2023-06-02T10:00:00.123Z",
        },
        "versions": {
            "1.0.0": {"dependencies": {"a": "1"}, "bundleDependencies": True},
            "1.1.0-beta.1": {},
            "1.1.0": {"dist": {"unpackedSize": 1234, "fileCount": 5},
                      "peerDependencies": {"x": "*"}},
        },
    }


def write_advisory(root, ghsa_id, published, severity, affected, withdrawn=None):
    path = Path(root) / "advisories" / "github-reviewed" / "2023" / "01" / ghsa_id
    path.mkdir(parents=True, exist_ok=True)
    data = {"id": ghsa_id, "published": published, "affected": affected,
            "database_specific": {"severity": severity} if severity else {}}
    if withdrawn:
        data["withdrawn"] = withdrawn
    (path / f"{ghsa_id}.json").write_text(json.dumps(data))


def npm(name):
    return {"package": {"ecosystem": "npm", "name": name}}


def make_advisory_db(root):
    # all IDs below are made-up test data
    write_advisory(root, "GHSA-test-0001", "2022-03-18T00:00:00Z", "CRITICAL", [npm("minimist")])
    write_advisory(root, "GHSA-test-0002", "2023-05-10T12:00:00Z", "MODERATE", [npm("minimist")])
    write_advisory(root, "GHSA-test-0003", "2024-02-15T08:30:00Z", "HIGH", [npm("minimist")])
    write_advisory(root, "GHSA-test-0004", "2024-04-15T08:30:00Z", "HIGH", [npm("minimist")])  # > 90 d
    write_advisory(root, "GHSA-test-0005", "2023-06-01T00:00:00Z", "LOW", [npm("minimist")],
                   withdrawn="2023-06-02T00:00:00Z")
    write_advisory(root, "GHSA-test-0006", "2023-06-01T00:00:00Z", "LOW",
                   [{"package": {"ecosystem": "PyPI", "name": "minimist"}}])
    write_advisory(root, "GHSA-test-0007", "2023-12-31T23:59:59Z", None,
                   [npm("minimist"), npm("minimist"), npm("@scope/other")])


class TimeTests(unittest.TestCase):
    def test_parse_and_format(self):
        dt = parse_ts("2023-02-09T20:11:17.6Z")
        self.assertEqual(format_ts(dt), "2023-02-09T20:11:17.600Z")
        self.assertEqual(format_ts(parse_ts("2023-02-09T22:00:00+02:00")), "2023-02-09T20:00:00.000Z")
        self.assertEqual(format_ts(parse_ts("2023-02-09T20:00:00Z")), "2023-02-09T20:00:00.000Z")

    def test_month_ranges_match_documentation(self):
        months = month_ranges(date(2026, 7, 1))
        self.assertEqual(months[0], (date(2026, 6, 1), date(2026, 7, 1)))  # M0 = June 2026
        self.assertEqual(months[11], (date(2025, 7, 1), date(2025, 8, 1)))  # M11 = July 2025


class RegistryTests(unittest.TestCase):
    def test_purl(self):
        self.assertEqual(registry.make_purl("react", "19.2.5"), "pkg:npm/react@19.2.5")
        self.assertEqual(registry.make_purl("@babel/core", "7.0.0"), "pkg:npm/%40babel/core@7.0.0")

    def test_stable(self):
        self.assertTrue(registry.is_stable("1.2.3"))
        self.assertFalse(registry.is_stable("2.0.0-beta.1"))
        self.assertFalse(registry.is_stable("1.2"))

    def test_minimist_rows(self):
        rows = registry.package_rows(minimist_doc(), PRED, MONTHS)
        # published in 2023: 0.2.3 and 1.2.8 (2023-02-09), 0.2.4 (2023-02-25); sorted by semver
        self.assertEqual([r["purl"] for r in rows],
                         ["pkg:npm/minimist@0.2.3", "pkg:npm/minimist@0.2.4", "pkg:npm/minimist@1.2.8"])
        last = rows[-1]
        self.assertEqual(last["published_at"], "2023-02-09T20:59:49.233Z")
        self.assertEqual(last["dependency_count"], 0)
        self.assertEqual(last["dev_dependency_count"], 9)
        self.assertEqual(last["package_stable_release_count_M10"], 3)  # Feb 2023
        self.assertEqual(sum(last[f"package_stable_release_count_M{i}"] for i in range(12)), 3)

    def test_scoped_doc(self):
        rows = registry.package_rows(scoped_doc(), PRED, MONTHS)
        self.assertEqual([r["purl"] for r in rows],
                         ["pkg:npm/%40scope/other@1.0.0", "pkg:npm/%40scope/other@1.1.0"])
        self.assertEqual(rows[0]["bundled_dependency_count"], 1)  # bundleDependencies: true
        self.assertEqual(rows[1]["peer_dependency_count"], 1)
        self.assertEqual((rows[1]["unpacked_size"], rows[1]["file_count"]), (1234, 5))
        self.assertIsNone(rows[0]["unpacked_size"])  # missing -> NULL
        self.assertEqual(sum(rows[0][f"package_stable_release_count_M{i}"] for i in range(12)), 2)


class DownloadTests(unittest.TestCase):
    def daily(self, skip_month=None):
        out, day = [], date(2023, 1, 1)
        while day < date(2024, 1, 1):
            if day.month != skip_month:
                out.append({"day": day.isoformat(), "downloads": 10})
            day += timedelta(days=1)
        return out

    def test_sums_and_days(self):
        out = downloads.monthly_downloads(self.daily(), MONTHS)
        self.assertEqual(out["package_downloads_M10"], 280)  # Feb 2023, 28 days
        self.assertEqual(out["package_download_days_observed_M10"], 28)
        self.assertEqual(out["package_downloads_M0"], 310)  # Dec 2023

    def test_missing_month_is_null_not_zero(self):
        out = downloads.monthly_downloads(self.daily(skip_month=3), MONTHS)  # March = M9
        self.assertIsNone(out["package_downloads_M9"])
        self.assertEqual(out["package_download_days_observed_M9"], 0)

    def test_failed_request_is_all_null(self):
        out = downloads.monthly_downloads(None, MONTHS)
        self.assertEqual(len(out), 24)
        self.assertTrue(all(v is None for v in out.values()))


class AdvisoryTests(unittest.TestCase):
    def test_features(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_advisory_db(tmp)
            by_pkg = advisories.load_npm_advisories(Path(tmp))
        self.assertEqual(set(by_pkg), {"minimist", "@scope/other"})
        feats = advisories.ghsa_features(by_pkg["minimist"], PRED, MONTHS)
        self.assertEqual(feats["ghsa_critical_count"], 1)  # 2022: total only
        self.assertEqual(feats["ghsa_moderate_count"], 1)
        self.assertEqual(feats["ghsa_moderate_count_M7"], 1)  # May 2023
        self.assertEqual(feats["ghsa_unknown_count"], 1)  # duplicate affected entries counted once
        self.assertEqual(feats["ghsa_unknown_count_M0"], 1)  # 2023-12-31 23:59:59
        self.assertEqual(feats["ghsa_low_count"], 0)  # withdrawn + PyPI ignored
        self.assertEqual(feats["ghsa_high_count"], 0)
        self.assertEqual(feats["target_ghsa_high_count_90d"], 1)  # 2024-02-15; the April one is outside
        self.assertEqual(sum(v for k, v in feats.items() if k.startswith("target_")), 1)


class EndToEndTests(unittest.TestCase):
    def test_build(self):
        docs = {"minimist": minimist_doc(), "@scope/other": scoped_doc()}
        daily = TestDaily.daily()
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            make_advisory_db(tmp / "db")
            info = build_dataset.run(
                tmp / "db", tmp / "out", PRED, ["ghost"],
                fetch_registry=lambda name: docs.get(name),
                fetch_downloads=lambda name, first, last: daily,
                workers=2,
            )
            self.assertEqual(info["rows"], 5)
            self.assertEqual(info["packages_with_rows"], 2)
            self.assertIn("ghost", (tmp / "out" / "errors.tsv").read_text())

            con = sqlite3.connect(tmp / "out" / "training_data.sqlite")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM training_data").fetchone()[0], 5)
            self.assertEqual(con.execute(
                "SELECT DISTINCT target_ghsa_high_count_90d FROM training_data "
                "WHERE purl LIKE 'pkg:npm/minimist@%'").fetchall(), [(1,)])
            self.assertEqual(con.execute(
                "SELECT ghsa_unknown_count_M0 FROM training_data "
                "WHERE purl = 'pkg:npm/%40scope/other@1.1.0'").fetchone(), (1,))
            con.close()

            with open(tmp / "out" / "training_data.csv", newline="") as fh:
                rows = list(csv.reader(fh))
            self.assertEqual(rows[0], schema.column_names())
            self.assertEqual(len(rows), 6)
            self.assertTrue(all(len(r) == 224 for r in rows))


class TestDaily:
    @staticmethod
    def daily():
        return DownloadTests().daily()


if __name__ == "__main__":
    unittest.main()
