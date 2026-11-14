"""Fetch issues and pull requests from GitHub for the packages of existing datasets.

    python -m npmdata.fetch_github data_full/training_data.sqlite [more.sqlite ...]

Reads each package's repository from the cached registry documents and stores one file per
repository in cache/github/. Stop and rerun at any time: finished repositories are skipped.
Set the environment variable GITHUB_TOKEN (5,000 requests per hour; 60 without a token).
The dataset build then fills the issue / PR columns from this cache with --github.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
from datetime import date
from pathlib import Path
from urllib.parse import quote, unquote

from . import github, sources

log = logging.getLogger(__name__)

DEFAULT_SINCE = date(2025, 4, 1)  # oldest month needed: M11 of the 2026-04-01 snapshot


def package_names(sqlite_paths: list[Path]) -> list[str]:
    names = set()
    for path in sqlite_paths:
        con = sqlite3.connect(path)
        try:
            for (purl,) in con.execute("SELECT DISTINCT purl FROM training_data"):
                names.add(unquote(purl[len("pkg:npm/"):].rsplit("@", 1)[0]))
        finally:
            con.close()
    return sorted(names)


def repositories(names: list[str], cache_dir: Path) -> dict[str, list[str]]:
    """{repo: [packages]} from the registry cache. Packages without a GitHub repo are left out."""
    repos: dict[str, list[str]] = {}
    for name in names:
        path = cache_dir / "registry" / (quote(name, safe="") + ".json")
        try:
            repo = github.repo_from_doc(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            repo = None
        if repo:
            repos.setdefault(repo, []).append(name)
    return repos


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("datasets", nargs="+", type=Path, help="training_data.sqlite files")
    parser.add_argument("--cache", default=Path("cache"), type=Path)
    parser.add_argument("--since", default=DEFAULT_SINCE, type=date.fromisoformat)
    parser.add_argument("--max-repos", type=int, help="smoke test: only the first N repositories")
    parser.add_argument("--max-pages", default=2000, type=int,
                        help="give up on a repository after this many pages (columns stay NULL)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.warning("GITHUB_TOKEN is not set: limited to 60 requests per hour")
    names = package_names(args.datasets)
    repos = sorted(repositories(names, args.cache))
    log.info("%d packages, %d GitHub repositories", len(names), len(repos))
    if args.max_repos:
        repos = repos[:args.max_repos]

    fetch = sources.github_fetcher(args.cache, token, args.since, args.max_pages)
    counts = {"found": 0, "missing": 0, "truncated": 0, "failed": 0}
    for n, repo in enumerate(repos, 1):
        try:
            doc = fetch(repo)
            key = "truncated" if doc["truncated"] else "found" if doc["found"] else "missing"
            counts[key] += 1
        except Exception as exc:  # keep going; rerun to retry
            counts["failed"] += 1
            log.warning("%s failed: %s: %s", repo, type(exc).__name__, exc)
        if n % 50 == 0 or n == len(repos):
            log.info("%d/%d repositories %s", n, len(repos), counts)


if __name__ == "__main__":
    main()
