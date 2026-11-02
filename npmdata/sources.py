"""Network access: npm registry and npm downloads API, with an on-disk cache.

Not exercised by the offline tests - verify on one package first (see README).
"""
from __future__ import annotations

import json
import logging
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
        path.write_text(json.dumps(value), encoding="utf-8")
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
