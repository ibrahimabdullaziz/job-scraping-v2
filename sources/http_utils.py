"""
Shared HTTP utilities for source adapters.

Provides a single get_text() helper with:
  - Real browser TLS fingerprint impersonation (via curl_cffi) to avoid Cloudflare 403 on cloud hosts (Railway, etc.).
  - Fallback to requests.Session with modern browser Client Hints (sec-ch-ua, etc.).
  - Rate-limit (429) awareness and exponential backoff.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

try:
    from curl_cffi import requests as cffi_requests
    _HAS_CURL_CFFI = True
except ImportError:
    cffi_requests = None
    _HAS_CURL_CFFI = False

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT: int = 20
DEFAULT_RETRIES: int = 3
DEFAULT_BACKOFF: float = 2.0

_CFFI_SESSION = None
_REQUESTS_SESSION: Optional[requests.Session] = None

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def _get_cffi_session():
    global _CFFI_SESSION
    if _CFFI_SESSION is None and _HAS_CURL_CFFI:
        try:
            _CFFI_SESSION = cffi_requests.Session(impersonate="chrome124")
            _CFFI_SESSION.headers.update(BROWSER_HEADERS)
        except Exception as e:
            log.warning("Could not initialize curl_cffi session: %s", e)
            _CFFI_SESSION = None
    return _CFFI_SESSION


def _get_requests_session() -> requests.Session:
    global _REQUESTS_SESSION
    if _REQUESTS_SESSION is None:
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
        s.headers.update(BROWSER_HEADERS)
        _REQUESTS_SESSION = s
    return _REQUESTS_SESSION


def get_text(url: str, *, timeout: int = DEFAULT_TIMEOUT, extra_headers: Optional[dict] = None) -> Optional[str]:
    """
    Fetch *url* and return the response body as text, or None on failure.
    Uses curl_cffi with Chrome TLS impersonation when available to avoid Cloudflare 403.
    """
    headers = dict(BROWSER_HEADERS)
    if extra_headers:
        headers.update(extra_headers)

    # 1. Try curl_cffi first if installed
    cffi_s = _get_cffi_session()
    if cffi_s is not None:
        try:
            resp = cffi_s.get(url, timeout=timeout, headers=headers)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "30"))
                log.warning("Rate-limited by %s — sleeping %ds", url, retry_after)
                time.sleep(min(retry_after, 60))
                resp = cffi_s.get(url, timeout=timeout, headers=headers)

            if resp.status_code == 200:
                return resp.text
            elif resp.status_code != 403:
                log.warning("HTTP %s from %s", resp.status_code, url)
                return None
            else:
                log.warning("HTTP 403 from %s via curl_cffi, attempting requests fallback", url)
        except Exception as exc:
            log.debug("curl_cffi error for %s (%s), attempting requests fallback", url, exc)

    # 2. Fallback to standard requests
    try:
        req_s = _get_requests_session()
        resp = req_s.get(url, timeout=timeout, headers=headers)

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "30"))
            log.warning("Rate-limited by %s — sleeping %ds", url, retry_after)
            time.sleep(min(retry_after, 60))
            resp = req_s.get(url, timeout=timeout, headers=headers)

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
