"""
Shared HTTP utilities for source adapters.

Provides a single get_text() helper with:
  - Browser-like headers to avoid trivial bot-detection.
  - Configurable retries with exponential backoff.
  - Structured logging on failures.
  - Rate-limit (429) awareness.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT: int = 20
DEFAULT_RETRIES: int = 3
DEFAULT_BACKOFF: float = 2.0

_SESSION: Optional[requests.Session] = None


def _build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=DEFAULT_RETRIES,
        backoff_factor=DEFAULT_BACKOFF,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    return s


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = _build_session()
    return _SESSION


def get_text(url: str, *, timeout: int = DEFAULT_TIMEOUT, extra_headers: Optional[dict] = None) -> Optional[str]:
    """
    Fetch *url* and return the response body as text, or None on failure.

    Handles 429 (rate-limited) with a brief sleep and one retry.
    """
    headers = extra_headers or {}
    try:
        resp = _session().get(url, timeout=timeout, headers=headers)

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "30"))
            log.warning("Rate-limited by %s — sleeping %ds", url, retry_after)
            time.sleep(min(retry_after, 60))
            resp = _session().get(url, timeout=timeout, headers=headers)

        if resp.status_code != 200:
            log.warning("HTTP %s from %s", resp.status_code, url)
            return None

        return resp.text

    except requests.RequestException as exc:
        log.error("Request failed for %s: %s", url, exc)
        return None


def get_json(url: str, *, timeout: int = DEFAULT_TIMEOUT, extra_headers: Optional[dict] = None) -> Optional[dict | list]:
    """Fetch *url* and parse as JSON, or return None on failure."""
    headers = {"Accept": "application/json", **(extra_headers or {})}
    text = get_text(url, timeout=timeout, extra_headers=headers)
    if text is None:
        return None
    import json
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        log.error("JSON parse error from %s: %s", url, exc)
        return None
