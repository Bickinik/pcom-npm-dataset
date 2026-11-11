import unittest
from datetime import date

from model import features, models
from model.backtest import metrics
from model.data import borrow_downloads

PRED = date(2026, 7, 1)


def record(purl, published, **extra):
    row = {"purl": purl, "package_created_at": "2020-01-01T00:00:00.000Z",
           "published_at": published, "dependency_count": 1}
    for sev in features.SEVERITIES + ["unknown"]:
        row[f"ghsa_{sev}_count"] = 0
        row[f"target_ghsa_{sev}_count_90d"] = 0
        for i in range(12):
            row[f"ghsa_{sev}_count_M{i}"] = 0
    row.update(extra)
    return row


class FeatureTests(unittest.TestCase):
    def test_split_purl(self):
        self.assertEqual(features.split_purl("pkg:npm/%40babel/core@7.0.0"), ("@babel/core", "7.0.0"))
        self.assertEqual(features.split_purl("pkg:npm/minimist@1.2.8"), ("minimist", "1.2.8"))

    def test_latest_is_highest_semver_not_newest(self):
        records = [
            record("pkg:npm/a@2.0.0", "2026-01-01T00:00:00.000Z", dependency_count=5),
            record("pkg:npm/a@1.9.9", "2026-06-01T00:00:00.000Z", dependency_count=9),
            record("pkg:npm/a@1.10.0", "2026-03-01T00:00:00.000Z", dependency_count=7),
        ]
        f = features.package_features(records, PRED)
        self.assertEqual(f["dependency_count"], 5)
        self.assertEqual(f["latest_major"], 2)
        self.assertEqual(f["versions_in_window"], 3)
        self.assertEqual(f["days_since_last_release"], 30)

    def test_ghsa_windows_and_csv_strings(self):
        r = record("pkg:npm/a@1.0.0", "2026-01-01T00:00:00.000Z",
                   ghsa_high_count="7", ghsa_high_count_M0="1", ghsa_high_count_M2="2",
                   ghsa_high_count_M11="1", ghsa_low_count=1, package_downloads_M0="",
                   target_ghsa_high_count_90d="3")
        f = features.package_features([r], PRED)
        self.assertEqual((f["ghsa_high_3m"], f["ghsa_high_12m"], f["ghsa_high_older"]), (3, 4, 3))
        self.assertEqual((f["ghsa_total"], f["ghsa_12m"]), (8, 4))
        self.assertIsNone(f["downloads_1m"])
        self.assertEqual(features.package_targets([r])["high"], 3)

    def test_ghsa_history_survives_per_version_counts(self):
        # Counted per version: the old version is affected, the latest one is fixed and shows 0.
        old = record("pkg:npm/a@1.0.0", "2026-01-01T00:00:00.000Z",
                     ghsa_high_count=5, ghsa_high_count_M1=2, ghsa_low_count_M4=1, ghsa_low_count=1)
        fixed = record("pkg:npm/a@1.1.0", "2026-05-01T00:00:00.000Z",
                       ghsa_high_count=1, ghsa_high_count_M0=1)
        f = features.package_features([old, fixed], PRED)
        self.assertEqual((f["ghsa_high_total"], f["ghsa_high_3m"], f["ghsa_high_12m"]), (5, 3, 3))
        self.assertEqual((f["ghsa_low_total"], f["ghsa_low_12m"]), (1, 1))
        # Counted per package: every record is the same, so the result is that record's values.
        same = [record(f"pkg:npm/a@1.{i}.0", "2026-01-01T00:00:00.000Z",
                       ghsa_high_count=5, ghsa_high_count_M1=2) for i in range(3)]
        self.assertEqual(features.package_features(same, PRED)["ghsa_high_total"],
                         features.package_features(same[:1], PRED)["ghsa_high_total"])

    def test_group_by_package(self):
        groups = features.group_by_package([{"purl": "pkg:npm/%40s/a@1.0.0"},
                                            {"purl": "pkg:npm/%40s/a@1.1.0"},
                                            {"purl": "pkg:npm/b@1.0.0"}])
        self.assertEqual({k: len(v) for k, v in groups.items()}, {"@s/a": 2, "b": 1})

    def test_borrow_downloads_shifts_months(self):
        donor = record("pkg:npm/a@1.0.0", "2026-01-01T00:00:00.000Z",
                       **{f"package_downloads_M{i}": 100 + i for i in range(12)})
        target = record("pkg:npm/a@0.9.0", "2026-01-01T00:00:00.000Z")
        other = record("pkg:npm/b@0.9.0", "2026-01-01T00:00:00.000Z")
        borrow_downloads([target, other], [donor], 3)
        self.assertEqual(target["package_downloads_M0"], 103)
        self.assertEqual(target["package_downloads_M8"], 111)
        self.assertIsNone(target["package_downloads_M9"])
        self.assertIsNone(other["package_downloads_M0"])


class ModelTests(unittest.TestCase):
    def test_naive_and_zero(self):
        r = record("pkg:npm/a@1.0.0", "2026-01-01T00:00:00.000Z", ghsa_high_count=4,
                   ghsa_high_count_M5=4)
        f = features.package_features([r], PRED)
        self.assertAlmostEqual(models.predict_naive(f)["high"], 4 * 90 / 365)
        self.assertEqual(models.predict_naive(f)["low"], 0)
        self.assertEqual(set(models.predict_zero(f)), set(features.SEVERITIES))

    def test_poisson_prediction_from_coefficients(self):
        r = record("pkg:npm/a@1.0.0", "2026-01-01T00:00:00.000Z")
        f = features.package_features([r], PRED)
        n = len(models.FEATURE_NAMES)
        model = {"kind": "poisson", "features": models.FEATURE_NAMES,
                 "coef": {sev: [-2.0] + [0.0] * n for sev in features.SEVERITIES}}
        pred = models.predict_poisson(model, f)
        self.assertAlmostEqual(pred["high"], 0.1353352832, places=6)
        self.assertEqual(len(models.feature_vector(f, "high")), n)

    def test_metrics(self):
        mae, rmse, dev = metrics([(1.0, 1.0), (0.0, 2.0)])
        self.assertAlmostEqual(mae, 1.0)
        self.assertAlmostEqual(rmse, 2 ** 0.5)
        self.assertGreater(dev, 0)


if __name__ == "__main__":
    unittest.main()
