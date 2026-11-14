"""GitHub issues and pull requests of a repository -> the monthly issue / PR columns.

The counts come from the dates stored on each issue and pull request (created, closed,
merged), not from the event history. Known limits of that:
- Only the last closure of an item is known, so repeated closures are counted once and an
  item that was closed, reopened and closed again counts as open in between.
- Reopening events are not available: the two *_reopened series stay NULL.
- The counts belong to the repository. Packages that share a repository get the same values.
"""
from __future__ import annotations

import re
from datetime import date

from .timeutil import N_MONTHS, parse_ts, to_dt

SERIES = [
    "issues_opened",
    "issues_closed",
    "issues_reopened",
    "pull_requests_opened",
    "pull_requests_closed_unmerged",
    "pull_requests_merged",
    "pull_requests_reopened",
    "issues_open_at_month_end",
    "pull_requests_open_at_month_end",
]

_URL = re.compile(r"github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.IGNORECASE)
_SHORT = re.compile(r"(?:github:)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


def repo_from_doc(doc: dict) -> str | None:
    """'owner/name' (lower case) from a registry document's repository field, else None."""
    repository = doc.get("repository")
    url = repository.get("url") if isinstance(repository, dict) else repository
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    match = _URL.search(url) or _SHORT.fullmatch(url)  # "owner/name" is npm's GitHub shorthand
    if not match:
        return None
    name = re.sub(r"\.git$", "", match.group(2))
    if not name or name in (".", ".."):
        return None
    return f"{match.group(1)}/{name}".lower()


def slim_item(item: dict) -> dict:
    """Keep only what the counts need from a GitHub API issue or pull request object."""
    pull = item.get("pull_request")  # present on issue objects that are pull requests
    is_pr = pull is not None or "merged_at" in item
    merged_at = (pull or {}).get("merged_at") if pull is not None else item.get("merged_at")
    return {
        "number": item["number"],
        "pr": is_pr,
        "created_at": item["created_at"],
        "closed_at": item.get("closed_at"),
        "merged_at": merged_at,
    }


def null_columns() -> dict:
    return {f"{series}_M{i}": None for series in SERIES for i in range(N_MONTHS)}


def monthly_github(repo_doc: dict | None, months: list[tuple[date, date]]) -> dict:
    """The nine monthly series for one repository; all NULL when the data is not usable.

    repo_doc is what sources.github_fetcher stores: {"found", "since", "truncated", "items"}.
    It must reach back to the start of the oldest month, otherwise nothing is filled.
    """
    out = null_columns()
    if not repo_doc or not repo_doc.get("found") or repo_doc.get("truncated"):
        return out
    if date.fromisoformat(repo_doc["since"]) > months[-1][0]:
        return out

    for series in SERIES:
        if not series.endswith("_reopened"):
            for i in range(N_MONTHS):
                out[f"{series}_M{i}"] = 0
    bounds = [(to_dt(start), to_dt(end)) for start, end in months]

    def add(series: str, moment) -> None:
        for i, (start, end) in enumerate(bounds):
            if start <= moment < end:
                out[f"{series}_M{i}"] += 1

    for item in repo_doc["items"]:
        kind = "pull_requests" if item["pr"] else "issues"
        created = parse_ts(item["created_at"])
        closed = parse_ts(item["closed_at"]) if item.get("closed_at") else None
        merged = parse_ts(item["merged_at"]) if item.get("merged_at") else None
        add(f"{kind}_opened", created)
        if item["pr"]:
            if merged:
                add("pull_requests_merged", merged)
            elif closed:
                add("pull_requests_closed_unmerged", closed)
        elif closed:
            add("issues_closed", closed)
        for i, (_, end) in enumerate(bounds):
            if created < end and (closed is None or closed >= end):
                out[f"{kind}_open_at_month_end_M{i}"] += 1
    return out
