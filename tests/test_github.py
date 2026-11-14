import tempfile
import unittest
from datetime import date
from pathlib import Path

from npmdata import github, sources
from npmdata.timeutil import month_ranges

MONTHS = month_ranges(date(2026, 7, 1))  # M0 = June 2026 ... M11 = July 2025


def item(number, created, closed=None, merged=None, pr=False):
    return {"number": number, "pr": pr, "created_at": created, "closed_at": closed,
            "merged_at": merged}


def repo_doc(items, since="2025-04-01", **extra):
    return {"repo": "o/r", "found": True, "since": since, "truncated": False,
            "items": items, **extra}


class RepoFromDocTests(unittest.TestCase):
    def test_url_forms(self):
        cases = {
            "git+https://github.com/expressjs/express.git": "expressjs/express",
            "git@github.com:Owner/Repo.git": "owner/repo",
            "https://github.com/a/b.js/tree/main/packages/x": "a/b.js",
            "github:a/b": "a/b",
            "a/b": "a/b",
            "https://gitlab.com/a/b": None,
            "gitlab:a/b": None,
            "": None,
        }
        for url, expected in cases.items():
            self.assertEqual(github.repo_from_doc({"repository": {"url": url}}), expected, url)
        self.assertEqual(github.repo_from_doc({"repository": "https://github.com/a/b"}), "a/b")
        self.assertIsNone(github.repo_from_doc({}))
        self.assertIsNone(github.repo_from_doc({"repository": {"type": "git"}}))


class MonthlyTests(unittest.TestCase):
    def test_counts(self):
        out = github.monthly_github(repo_doc([
            item(1, "2020-01-01T00:00:00Z"),                                   # old, still open
            item(2, "2026-06-10T00:00:00Z", closed="2026-06-20T00:00:00Z"),    # in and out in M0
            item(3, "2026-05-31T23:59:59Z", closed="2026-06-01T00:00:00Z"),    # month boundary
            item(4, "2026-06-05T00:00:00Z", closed="2026-06-06T00:00:00Z",
                 merged="2026-06-06T00:00:00Z", pr=True),
            item(5, "2026-04-01T00:00:00Z", closed="2026-06-30T00:00:00Z", pr=True),
            item(6, "2026-07-02T00:00:00Z", pr=True),                          # after prediction
        ]), MONTHS)
        self.assertEqual(out["issues_opened_M0"], 1)
        self.assertEqual(out["issues_opened_M1"], 1)
        self.assertEqual(out["issues_closed_M0"], 2)
        self.assertEqual(out["issues_open_at_month_end_M0"], 1)   # only the old one
        self.assertEqual(out["issues_open_at_month_end_M1"], 2)   # old one and number 3
        self.assertEqual(out["issues_open_at_month_end_M11"], 1)
        self.assertEqual(out["pull_requests_opened_M0"], 1)
        self.assertEqual(out["pull_requests_opened_M2"], 1)
        self.assertEqual(out["pull_requests_merged_M0"], 1)
        self.assertEqual(out["pull_requests_closed_unmerged_M0"], 1)
        self.assertEqual(out["pull_requests_open_at_month_end_M0"], 0)
        self.assertEqual(out["pull_requests_open_at_month_end_M1"], 1)
        self.assertIsNone(out["issues_reopened_M0"])
        self.assertIsNone(out["pull_requests_reopened_M5"])
        self.assertEqual(len(out), 9 * 12)

    def test_empty_repository_gives_zeros(self):
        out = github.monthly_github(repo_doc([]), MONTHS)
        self.assertEqual(out["issues_opened_M3"], 0)
        self.assertIsNone(out["issues_reopened_M3"])

    def test_unusable_data_gives_null(self):
        unusable = [None, {"repo": "o/r", "found": False}, repo_doc([], truncated=True),
                    repo_doc([], since="2025-08-01")]  # does not reach back to July 2025
        for doc in unusable:
            out = github.monthly_github(doc, MONTHS)
            self.assertTrue(all(v is None for v in out.values()), doc)
            self.assertEqual(len(out), 9 * 12)

    def test_slim_item(self):
        issue = {"number": 1, "created_at": "a", "closed_at": None, "title": "x"}
        pr_as_issue = {"number": 2, "created_at": "a", "closed_at": "b",
                       "pull_request": {"merged_at": "b"}}
        pr = {"number": 3, "created_at": "a", "closed_at": "b", "merged_at": None}
        self.assertEqual(github.slim_item(issue), item(1, "a"))
        self.assertEqual(github.slim_item(pr_as_issue), item(2, "a", "b", "b", pr=True))
        self.assertEqual(github.slim_item(pr), item(3, "a", "b", pr=True))


class FetcherTests(unittest.TestCase):
    """The fetcher with a fake page getter (no network)."""

    def run_fetch(self, pages, max_pages=2000):
        calls = []

        def get(url, token):
            calls.append(url)
            result = pages[len(calls) - 1]
            if isinstance(result, Exception):
                raise result
            return result

        with tempfile.TemporaryDirectory() as tmp:
            fetch = sources.github_fetcher(Path(tmp), None, date(2025, 4, 1), max_pages, get=get)
            doc = fetch("o/r")
            again = fetch("o/r")  # second call comes from the cache
            self.assertEqual(doc, again)
        return doc, calls

    def test_pages_and_deduplication(self):
        a = {"number": 1, "created_at": "2026-01-01T00:00:00Z", "closed_at": None}
        b = {"number": 2, "created_at": "2026-01-02T00:00:00Z", "closed_at": None,
             "pull_request": {"merged_at": None}}
        doc, calls = self.run_fetch([([a], "next-url"), ([b], None), ([a, b], None)])
        self.assertEqual([i["number"] for i in doc["items"]], [1, 2])
        self.assertEqual((doc["found"], doc["truncated"], doc["pages"]), (True, False, 3))
        self.assertEqual(calls[1], "next-url")
        self.assertIn("state=all&since=2025-04-01T00:00:00Z", calls[0])
        self.assertIn("state=open", calls[2])

    def test_missing_repository(self):
        doc, calls = self.run_fetch([(None, None)])
        self.assertEqual((doc["found"], doc["items"], len(calls)), (False, [], 1))

    def test_issues_switched_off_uses_pull_requests(self):
        new = {"number": 7, "created_at": "2026-01-01T00:00:00Z", "closed_at": None,
               "merged_at": None, "updated_at": "2026-01-01T00:00:00Z"}
        old = dict(new, number=3, updated_at="2024-01-01T00:00:00Z")
        doc, calls = self.run_fetch([sources.GithubGone("x"), ([new, old], "more"), ([], None)])
        self.assertEqual([i["number"] for i in doc["items"]], [7])  # stops at the old one
        self.assertTrue(doc["items"][0]["pr"])
        self.assertIn("/pulls?state=all", calls[1])
        self.assertIn("/pulls?state=open", calls[2])

    def test_too_many_pages_is_truncated(self):
        a = {"number": 1, "created_at": "2026-01-01T00:00:00Z", "closed_at": None}
        doc, _ = self.run_fetch([([a], "n1"), ([a], "n2"), ([a], "n3")], max_pages=2)
        self.assertTrue(doc["truncated"])
        out = github.monthly_github(doc, MONTHS)
        self.assertIsNone(out["issues_opened_M0"])


if __name__ == "__main__":
    unittest.main()
