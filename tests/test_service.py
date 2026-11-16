import csv
import io
import unittest
from unittest import mock

try:
    from fastapi.testclient import TestClient
    from model import service
except ImportError:  # the service needs requirements-service.txt
    service = None

from model.features import SEVERITIES


def records_csv(packages=("a",), versions=("1.0.0", "1.1.0"), purl=True) -> str:
    rows = []
    for name in packages:
        for version in versions:
            row = {"purl": f"pkg:npm/{name}@{version}"} if purl else {}
            row.update({
                   "package_created_at": "2020-01-01T00:00:00.000Z",
                   "published_at": "2026-01-01T00:00:00.000Z", "dependency_count": "1",
                   "package_downloads_M0": ""})  # empty cell, as in a CSV with NULLs
            for sev in SEVERITIES + ["unknown"]:
                row[f"ghsa_{sev}_count"] = "2"
                for i in range(12):
                    row[f"ghsa_{sev}_count_M{i}"] = "0"
            rows.append(row)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


class FakeResponse:
    status_code = 200
    text = '{"score": 1}'

    def __init__(self, content=b""):
        self.content = content

    def raise_for_status(self):
        pass


@unittest.skipIf(service is None, "fastapi or lightgbm is not installed")
class ServiceTests(unittest.TestCase):
    def run_round(self, get):
        """One round with a fake network -> the uploaded CSV as a list of rows."""
        with mock.patch.object(service.requests, "get", side_effect=get), \
                mock.patch.object(service.requests, "post", return_value=FakeResponse()) as post:
            client = TestClient(service.app)
            reply = client.post("/predict", json={"testdata_download_endpoint": "http://x/in",
                                                  "prediction_upload_endpoint": "http://x/out"})
        self.assertEqual(reply.json(), {"status": "prediction started"})
        self.assertEqual(post.call_args.args[0], "http://x/out")
        name, text, kind = post.call_args.kwargs["files"]["predictions"]
        self.assertEqual((name, kind), ("prediction.csv", "text/csv"))
        return list(csv.reader(io.StringIO(text)))

    def check_shape(self, rows):
        self.assertEqual(len(rows), 2)  # header and exactly one record
        self.assertEqual(rows[0], [f"target_ghsa_{s}_count_90d" for s in SEVERITIES + ["unknown"]])
        values = [float(v) for v in rows[1]]
        self.assertTrue(all(v >= 0 for v in values))
        return values

    def test_health(self):
        self.assertEqual(TestClient(service.app).get("/health").json(), {"health": "up"})

    def test_round(self):
        data = ("﻿" + records_csv()).encode("utf-8")  # with a byte order mark
        values = self.check_shape(self.run_round(lambda url, timeout: FakeResponse(data)))
        self.assertGreater(sum(values[:4]), 0)
        self.assertEqual(values[4], 0)  # unknown

    def test_round_without_purl_column(self):
        data = records_csv(purl=False).encode("utf-8")
        values = self.check_shape(self.run_round(lambda url, timeout: FakeResponse(data)))
        self.assertGreater(sum(values[:4]), 0)

    def test_several_packages_still_give_one_record(self):
        data = records_csv(packages=("a", "b")).encode("utf-8")
        self.check_shape(self.run_round(lambda url, timeout: FakeResponse(data)))

    def test_failures_upload_zeros(self):
        def broken(url, timeout):
            raise ConnectionError("no network")

        for get in (broken, lambda url, timeout: FakeResponse(b""),
                    lambda url, timeout: FakeResponse(b"not,a,records,file\n1,2,3,4\n")):
            self.assertEqual(self.check_shape(self.run_round(get)), [0, 0, 0, 0, 0])


if __name__ == "__main__":
    unittest.main()
