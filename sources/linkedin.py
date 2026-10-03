"""
LinkedIn source adapter (limited public guest search).

This adapter uses the public LinkedIn Jobs guest API endpoint and parses
only search-card fields: title, company, location, URL, and work-type signals.

Limitations and design decisions:
  - No authentication, no profile access, no descriptions.
  - LinkedIn changes its public HTML and API contract without notice.
    This source is intentionally fragile and should enrich WUZZUF, not replace it.
  - Rate limits are respected with configurable delays between requests.
  - The freshness window defaults to 1 hour (f_TPR=r3600) to tolerate a
    10-minute polling interval and occasional delayed scheduling.

Geographic scope: Egypt, Saudi Arabia, UAE, and remote.
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

from models import Job
from sources.http_utils import get_text
import config

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
BASE_URL = "https://www.linkedin.com"
PAGE_SIZE = 25

# ─── Search queries ───────────────────────────────────────────────────────────
# f_WT=2  → Remote; f_TPR=r3600 → last hour; sortBy=DD → newest first
# Keep query count manageable to avoid hitting rate limits quickly.

def _params(**kw) -> dict[str, str]:
    base = {
        "f_TPR": f"r{config.LINKEDIN_FRESHNESS_SECONDS}",
        "sortBy": "DD",
    }
    base.update(kw)
    return base


LINKEDIN_SEARCHES: list[dict[str, str]] = [
    # ── Egypt ──────────────────────────────────────────────────────────────
    _params(keywords="software engineer", location="Egypt"),
    _params(keywords="backend developer", location="Egypt"),
    _params(keywords="frontend developer", location="Egypt"),
    _params(keywords="full stack developer", location="Egypt"),
    _params(keywords="mobile developer", location="Egypt"),
    _params(keywords="devops engineer", location="Egypt"),
    _params(keywords="data engineer", location="Egypt"),
    _params(keywords="data analyst", location="Egypt"),
    _params(keywords="machine learning engineer", location="Egypt"),
    _params(keywords="QA engineer", location="Egypt"),
    _params(keywords="cybersecurity analyst", location="Egypt"),
    _params(keywords="product manager", location="Egypt"),
    _params(keywords="ui ux designer", location="Egypt"),
    _params(keywords="IT support", location="Egypt"),
    _params(keywords="business analyst", location="Egypt"),
    # ── Saudi Arabia ───────────────────────────────────────────────────────
    _params(keywords="software engineer", location="Saudi Arabia"),
    _params(keywords="data analyst", location="Saudi Arabia"),
    _params(keywords="devops engineer", location="Saudi Arabia"),
    _params(keywords="cybersecurity analyst", location="Saudi Arabia"),
    _params(keywords="IT support", location="Saudi Arabia"),
    _params(keywords="product manager", location="Saudi Arabia"),
    # ── UAE ────────────────────────────────────────────────────────────────
    _params(keywords="software engineer", location="United Arab Emirates"),
    _params(keywords="data analyst", location="United Arab Emirates"),
    _params(keywords="devops engineer", location="United Arab Emirates"),
    _params(keywords="cybersecurity analyst", location="United Arab Emirates"),
    _params(keywords="IT support", location="United Arab Emirates"),
    # ── Remote (f_WT=2 → LinkedIn remote filter) ──────────────────────────
    _params(keywords="software engineer", f_WT="2"),
    _params(keywords="data engineer", f_WT="2"),
    _params(keywords="devops engineer", f_WT="2"),
    _params(keywords="data analyst", f_WT="2"),
    _params(keywords="product manager", f_WT="2"),
]

# ─── Regex parsers ────────────────────────────────────────────────────────────
CARD_RE = re.compile(
    r"<li\b[^>]*>(?P<card>.*?)</li>",
    re.IGNORECASE | re.DOTALL,
)
TITLE_RE = re.compile(
    r'class=["\'][^"\']*base-search-card__title[^"\']*["\'][^>]*>(?P<title>.*?)</(?:h3|h4|a|span|div)>',
    re.IGNORECASE | re.DOTALL,
)
COMPANY_RE = re.compile(
    r'class=["\'][^"\']*base-search-card__subtitle[^"\']*["\'][^>]*>(?P<company>.*?)</(?:h4|a|span|div)>',
    re.IGNORECASE | re.DOTALL,
)
LOCATION_RE = re.compile(
    r'class=["\'][^"\']*job-search-card__location[^"\']*["\'][^>]*>(?P<location>.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)
URL_RE = re.compile(
    r'href=["\'](?P<url>https?://[^"\']*linkedin\.com/jobs/view/[^"\']+|/jobs/view/[^"\']+)["\']',
    re.IGNORECASE,
)
JOB_ID_RE = re.compile(
    r"/jobs/view/(?:[^/?#]*-)?(?P<id>\d+)(?:[/?#]|$)",
    re.IGNORECASE,
)
TAG_RE = re.compile(
    r'<span\b[^>]*class=["\'][^"\']*job-search-card__job-insight[^"\']*["\'][^>]*>(?P<tag>.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)

TAG_TYPE_PATTERNS: tuple[str, ...] = (
    "Full-time", "Part-time", "Contract", "Temporary",
    "Internship", "Volunteer", "Remote", "Hybrid", "On-site",
)

REMOTE_MARKERS: tuple[str, ...] = ("remote", "work from home", "wfh")

CLOSED_MARKERS: tuple[str, ...] = (
    "no longer accepting applications",
    "this job is no longer",
)


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _is_closed(card: str) -> bool:
    lower = card.lower()
    return any(m in lower for m in CLOSED_MARKERS)


def _parse_cards(html_text: str, location_hint: str = "") -> list[Job]:
    """Parse job cards from a LinkedIn guest search result page."""
    jobs: list[Job] = []
    for m in CARD_RE.finditer(html_text):
        card = m.group("card")
        if not card.strip():
            continue
        if _is_closed(card):
            continue

        # Title
        tm = TITLE_RE.search(card)
        if not tm:
            continue
        title = _clean(tm.group("title"))
        if not title:
            continue

        # URL
        um = URL_RE.search(card)
        if not um:
            continue
        url_raw = um.group("url")
        url = urljoin(BASE_URL, url_raw.split("?")[0])

        # Job ID (LinkedIn numeric)
        id_m = JOB_ID_RE.search(url_raw)
        source_job_id = id_m.group("id") if id_m else ""

        # Company
        cm = COMPANY_RE.search(card)
        company = _clean(cm.group("company")) if cm else ""

        # Location
        lm = LOCATION_RE.search(card)
        location = _clean(lm.group("location")) if lm else location_hint

        # Tags
        tags = []
        for tag_m in TAG_RE.finditer(card):
            tag_text = _clean(tag_m.group("tag"))
            for pat in TAG_TYPE_PATTERNS:
                if pat.lower() in tag_text.lower():
                    tags.append(pat)

        # Work arrangement
        combined = (location + " " + " ".join(tags)).lower()
        is_remote = any(mk in combined for mk in REMOTE_MARKERS)

        if is_remote:
            work_arrangement = "Remote"
        elif "hybrid" in combined:
            work_arrangement = "Hybrid"
        elif "on-site" in combined:
            work_arrangement = "On-site"
        else:
            work_arrangement = ""

        jobs.append(
            Job(
                title=title,
                company=company,
                location=location,
                url=url,
                source="LinkedIn",
                source_job_id=source_job_id,
                work_arrangement=work_arrangement,
                is_remote=is_remote,
                tags=tags,
            )
        )

    return jobs


def _build_search_url(params: dict[str, str], start: int = 0) -> str:
    from urllib.parse import urlencode
    all_params = {**params, "start": str(start)}
    return f"{SEARCH_URL}?{urlencode(all_params)}"


def fetch_linkedin(
    searches: Optional[list[dict[str, str]]] = None,
    max_pages_per_search: int = None,
    request_delay: float = None,
    http_getter: Optional[Callable[[str], Optional[str]]] = None,
) -> list[Job]:
    """
    Fetch public LinkedIn guest search results and return normalised Job records.

    Args:
        searches: Override the default search-parameter list (for tests).
        max_pages_per_search: Pages to read per query (default from config).
        request_delay: Sleep between requests in seconds (default from config).
        http_getter: Injectable HTTP getter for tests.
    """
    getter = http_getter or (lambda url: get_text(url, extra_headers={
        "Accept": "text/html,application/json",
        "Referer": "https://www.linkedin.com/jobs/search/",
    }))

    query_list = searches if searches is not None else LINKEDIN_SEARCHES
    pages = max_pages_per_search if max_pages_per_search is not None else config.LINKEDIN_MAX_PAGES_PER_SEARCH
    delay = request_delay if request_delay is not None else config.LINKEDIN_REQUEST_DELAY

    all_jobs: list[Job] = []
    seen_ids: set[str] = set()

    for params in query_list:
        location_hint = params.get("location", "")
        for page_idx in range(pages):
            start = page_idx * PAGE_SIZE
            url = _build_search_url(params, start=start)
            log.info("LinkedIn fetching: %s", url)

            html_text = getter(url)
            if html_text is None:
                log.warning("LinkedIn: no response for params=%s page=%d", params, page_idx)
                break

            page_jobs = _parse_cards(html_text, location_hint=location_hint)
            if not page_jobs:
                log.debug("LinkedIn: empty page for params=%s page=%d", params, page_idx)
                break

            for job in page_jobs:
                key = job.source_job_id or job.canonical_url
                if key and key not in seen_ids:
                    seen_ids.add(key)
                    all_jobs.append(job)

            time.sleep(delay)

        time.sleep(delay)

    log.info("LinkedIn total fetched: %d jobs", len(all_jobs))
    return all_jobs
