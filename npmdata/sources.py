"""Network access: npm registry and npm downloads API, with an on-disk cache.

Not exercised by the offline tests - verify on one package first (see README).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger(__name__)

USER_AGENT = "pcom-npm-dataset/0.1 (university coursework)"


def fetch_json(url: str, retries: int = 5, timeout: int = 60):
    """GET url as JSON. Returns None on 404; retries on 429/5xx/network errors."""
    reason = "unknown"
    for attempt in range(retries):
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        wait = min(60, 2**attempt)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            if err.code not in (429, 500, 502, 503, 504):
                raise
            reason = f"HTTP {err.code}"
            retry_after = err.headers.get("Retry-After") if err.headers else None
            if retry_after and retry_after.isdigit():
                wait = min(120, max(2, int(retry_after)))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
        log.warning("retrying %s in %ss (%s)", url, wait, reason)
        time.sleep(wait)
    raise RuntimeError(f"giving up on {url} ({reason})")


def _cached(path: Path, produce):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    value = produce()
    if value is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file first, so a stopped run never leaves a half-written cache file.
        tmp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(value), encoding="utf-8")
        os.replace(tmp, path)
    return value


def registry_fetcher(cache_dir: Path):
    def fetch(name: str):
        url = "https://registry.npmjs.org/" + quote(name, safe="@").replace("/", "%2F")
        return _cached(cache_dir / "registry" / (quote(name, safe="") + ".json"),
                       lambda: fetch_json(url))

    return fetch


def downloads_fetcher(cache_dir: Path):
    """Returns the daily list for first..last (inclusive), or None if unavailable."""

    def fetch(name: str, first: date, last: date):
        def attempt(path_name: str):
            url = f"https://api.npmjs.org/downloads/range/{first}:{last}/{path_name}"
            return fetch_json(url, retries=3)

        def produce():
            try:
                return attempt(name)
            except Exception:
                if name.startswith("@"):  # second try with the slash of the scope encoded
                    return attempt(name.replace("/", "%2F"))
                raise

        doc = _cached(
            cache_dir / "downloads" / f"{quote(name, safe='')}_{first}_{last}.json", produce
        )
        if not doc or "downloads" not in doc:
            return None
        return doc["downloads"]

    return fetch


def last_day_before(prediction_date: date) -> date:
    return prediction_date - timedelta(days=1)


# --- GitHub ---------------------------------------------------------------------------------

class GithubGone(Exception):
    """HTTP 410: the repository has issues switched off."""


def github_get(url: str, token: str | None, retries: int = 6, timeout: int = 60):
    """One page of the GitHub API -> (json, url of the next page or None).

    (None, None) when the repository does not exist or is blocked. Waits when the rate limit
    is used up (the response says when it resets).
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    reason = "unknown"
    attempt = 0
    while attempt < retries:
        wait = min(60, 2**attempt)
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                link = response.headers.get("Link") or ""
                data = json.load(response)
            for part in link.split(","):
                target, _, rel = part.partition(";")
                if 'rel="next"' in rel:
                    return data, target.strip()[1:-1]
            return data, None
        except urllib.error.HTTPError as err:
            if err.code in (404, 451):
                return None, None
            if err.code == 410:
                raise GithubGone(url) from None
            info = err.headers or {}
            if err.code in (403, 429) and info.get("X-RateLimit-Remaining") == "0":
                reset = int(info.get("X-RateLimit-Reset") or 0)
                pause = min(3700, max(5, reset - int(time.time()) + 5))
                log.warning("GitHub rate limit used up, waiting %d s", pause)
                time.sleep(pause)
                continue  # not a failed attempt
            if err.code in (403, 429) and (info.get("Retry-After") or "").isdigit():
                wait = min(600, max(5, int(info["Retry-After"])))
            elif err.code not in (429, 500, 502, 503, 504):
                raise
            reason = f"HTTP {err.code}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
        attempt += 1
        log.warning("retrying %s in %ss (%s)", url, wait, reason)
        time.sleep(wait)
    raise RuntimeError(f"giving up on {url} ({reason})")


def github_cache_path(cache_dir: Path, repo: str, since: date) -> Path:
    return cache_dir / "github" / f"{quote(repo, safe='')}_since_{since}.json"


def github_cached(cache_dir: Path, since: date):
    """Read-only lookup for the dataset build: repo -> stored document or None. No network."""
    def lookup(repo: str):
        path = github_cache_path(cache_dir, repo, since)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    return lookup


def github_fetcher(cache_dir: Path, token: str | None, since: date, max_pages: int = 2000,
                   get=github_get):
    """repo ('owner/name') -> {"repo", "found", "since", "truncated", "items"}, cached.

    items are all issues and pull requests that were changed on or after `since` or are open
    now. That is every item that was opened, closed or open at any time from `since` on.
    """
    from .github import slim_item

    def fetch(repo: str):
        base = f"https://api.github.com/repos/{repo}"
        items: dict[int, dict] = {}
        pages = 0

        def collect(url: str | None, stop_before: str | None = None) -> bool:
            """False when the repository is missing. stop_before: stop at older updated_at."""
            nonlocal pages
            while url and pages < max_pages:
                data, url = get(url, token)
                pages += 1
                if data is None:
                    return False
                for item in data:
                    if stop_before and item.get("updated_at", "9") < stop_before:
                        return True
                    items[item["number"]] = slim_item(item)
            return True

        def produce():
            stamp = f"{since}T00:00:00Z"
            try:
                found = (collect(f"{base}/issues?state=all&since={stamp}&per_page=100")
                         and collect(f"{base}/issues?state=open&per_page=100"))
            except GithubGone:  # no issue tracker: pull requests only
                items.clear()
                found = (collect(f"{base}/pulls?state=all&sort=updated&direction=desc"
                                 "&per_page=100", stop_before=stamp)
                         and collect(f"{base}/pulls?state=open&per_page=100"))
            return {
                "repo": repo,
                "found": found,
                "since": str(since),
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "pages": pages,
                "truncated": pages >= max_pages,
                "items": sorted(items.values(), key=lambda i: i["number"]) if found else [],
            }

        return _cached(github_cache_path(cache_dir, repo, since), produce)

    return fetch
