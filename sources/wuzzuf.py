"""
WUZZUF source adapter.

Fetches public WUZZUF category/search pages and converts visible job cards
into normalised Job records.

This module intentionally:
  - Reads only public, unauthenticated search result pages.
  - Parses only card-level data visible on the listing page.
  - Does NOT open individual job detail pages.
  - Does NOT collect recruiter profiles or private user data.
"""

from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

from models import Job
from sources.http_utils import get_text

log = logging.getLogger(__name__)

BASE_URL = "https://wuzzuf.net"

# ─── Public category/search pages ────────────────────────────────────────────
# Keep this list intentionally narrow.  Add new categories after verifying they
# return relevant tech/IT roles.
WUZZUF_SEARCH_URLS: list[str] = [
    "https://wuzzuf.net/a/Software-Development-Jobs-in-Egypt",
    "https://wuzzuf.net/a/Software-Engineering-Jobs-in-Egypt",
    "https://wuzzuf.net/a/Information-Technology-IT-Jobs-in-Egypt",
    "https://wuzzuf.net/a/Android-Jobs-in-Egypt",
    "https://wuzzuf.net/a/Creative-Design-Art-Jobs-in-Egypt",
    "https://wuzzuf.net/a/Analyst-Research-Jobs-in-Egypt",
    "https://wuzzuf.net/a/Project-Program-Management-Jobs-in-Egypt",
    "https://wuzzuf.net/a/Internships-in-Egypt",
    "https://wuzzuf.net/a/work-from-home",
    # Saudi Arabia & UAE — WUZZUF occasionally lists roles there too
    "https://wuzzuf.net/a/Software-Engineering-Jobs-in-Saudi-Arabia",
    "https://wuzzuf.net/a/Information-Technology-IT-Jobs-in-Saudi-Arabia",
]

# ─── Regex helpers ────────────────────────────────────────────────────────────
JOB_LINK_RE = re.compile(
    r'<a\b[^>]*href=["\'](?P<href>[^"\']*\/jobs\/p\/[^"\']+)["\'][^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
CAREER_LINK_RE = re.compile(
    r'<a\b[^>]*href=[^>]*/jobs/careers/[^"\']+["\'][^>]*>(?P<company>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
JOB_ID_RE = re.compile(r"/jobs/p/([^/?#]+)")
TAG_RE = re.compile(
    r'<a\b[^>]*>(?P<text>.*?)</a>|<span\b[^>]*>(?P<text2>.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)

JOB_TYPE_PATTERNS: tuple[str, ...] = (
    "Full Time", "Part Time", "Internship", "Freelance / Project",
    "Freelance", "Shift Based", "Volunteering",
    "دوام كامل", "دوام جزئي", "تدريب عملي",
)
WORKPLACE_PATTERNS: tuple[str, ...] = (
    "Remote", "Hybrid", "On-site", "Work From Home",
    "عمل عن بُعد", "عمل من المنزل", "عمل من مقر الشركة", "هجين",
)
REMOTE_MARKERS: tuple[str, ...] = ("remote", "work from home", "عمل عن بُعد", "عمل من المنزل")

WORKPLACE_NORMALISE: dict[str, str] = {
    "remote": "Remote",
    "work from home": "Remote",
    "عمل عن بُعد": "Remote",
    "عمل من المنزل": "Remote",
    "hybrid": "Hybrid",
    "هجين": "Hybrid",
    "on-site": "On-site",
    "عمل من مقر الشركة": "On-site",
}

# ─── Card block pattern ───────────────────────────────────────────────────────
# WUZZUF wraps each job card in <div class="... css-1gatmva e1v1l3u10 ...">
CARD_DIV_RE = re.compile(
    r'<div\b[^>]*class=["\'][^"\']*css-1gatmva[^"\']*["\'][^>]*>(.*?)</div\s*>(?=\s*<div|$)',
    re.IGNORECASE | re.DOTALL,
)
# Fallback: look for anything around /jobs/p/ hrefs
NEXT_PAGE_RE = re.compile(
    r'href=["\']([^"\']*\?(?:[^"\']*&)?page=(\d+)[^"\']*)["\']',
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _extract_tags(block: str, patterns: tuple) -> list[str]:
    found: list[str] = []
    for m in TAG_RE.finditer(block):
        text = _clean(m.group("text") or m.group("text2") or "")
        for pat in patterns:
            if pat.lower() in text.lower():
                found.append(pat)
    return found


def _parse_cards(html_text: str) -> list[Job]:
    """Parse all job cards from a WUZZUF listing page."""
    jobs: list[Job] = []
    seen_ids: set[str] = set()

    # Find all job links — each one corresponds to a job card
    for m in JOB_LINK_RE.finditer(html_text):
        href = m.group("href")
        raw_title = _clean(m.group("title"))
        if not raw_title or not href:
            continue

        # Absolute URL
        url = urljoin(BASE_URL, href).split("?")[0]

        # Extract source job ID from URL
        id_m = JOB_ID_RE.search(href)
        if not id_m:
            continue
        source_job_id = id_m.group(1)
        if source_job_id in seen_ids:
            continue
        seen_ids.add(source_job_id)

        # Grab context windows:
        # - look-behind (for company name which often comes before the job link)
        # - look-ahead only (for job-type/workplace tags which always come after the link)
        link_pos = m.start()
        link_end = m.end()
        lookbehind = html_text[max(0, link_pos - 200): link_pos]
        lookahead = html_text[link_end: link_end + 600]
        context = lookbehind + lookahead

        # Company name (can appear before or just after the link)
        comp_m = CAREER_LINK_RE.search(context)
        company = _clean(comp_m.group("company")) if comp_m else ""

        # Job type and workplace tags appear AFTER the job title link
        job_types = _extract_tags(lookahead, JOB_TYPE_PATTERNS)
        workplaces = _extract_tags(lookahead, WORKPLACE_PATTERNS)

        job_type = job_types[0] if job_types else ""
        raw_wp = workplaces[0] if workplaces else ""
        work_arrangement = WORKPLACE_NORMALISE.get(raw_wp.lower(), raw_wp)

        is_remote = any(mk in (work_arrangement.lower() + " " + job_type.lower()) for mk in REMOTE_MARKERS)

        # Location: WUZZUF always shows Egypt unless it's a remote listing
        if is_remote:
            location = "Remote"
        else:
            location = "Egypt"

        jobs.append(
            Job(
                title=raw_title,
                company=company,
                location=location,
                url=url,
                source="WUZZUF",
                source_job_id=source_job_id,
                job_type=job_type,
                work_arrangement=work_arrangement,
                is_remote=is_remote,
                tags=job_types + workplaces,
            )
        )

    log.debug("WUZZUF parsed %d jobs from page", len(jobs))
    return jobs


def fetch_wuzzuf(
    search_urls: Optional[list[str]] = None,
    max_pages_per_search: int = 1,
    http_getter: Optional[Callable[[str], Optional[str]]] = None,
    request_delay: float = 1.5,
) -> list[Job]:
    """
    Fetch public WUZZUF pages and return normalised Job records.

    Args:
        search_urls: Override the default URL list (useful in tests).
        max_pages_per_search: How many paginated pages to read per URL.
        http_getter: Injectable HTTP getter (for tests).
        request_delay: Seconds to sleep between requests.
    """
    getter = http_getter or get_text
    urls = search_urls if search_urls is not None else WUZZUF_SEARCH_URLS

    all_jobs: list[Job] = []
    seen_ids: set[str] = set()

    for base_url in urls:
        for page in range(max_pages_per_search):
            url = base_url if page == 0 else f"{base_url}?page={page + 1}"
            log.info("WUZZUF fetching: %s", url)
            html_text = getter(url)
            if html_text is None:
                log.warning("WUZZUF: no response from %s", url)
                break

            page_jobs = _parse_cards(html_text)
            for job in page_jobs:
                if job.source_job_id not in seen_ids:
                    seen_ids.add(job.source_job_id)
                    all_jobs.append(job)

            if page < max_pages_per_search - 1:
                time.sleep(request_delay)

        time.sleep(request_delay)

    log.info("WUZZUF total fetched: %d jobs", len(all_jobs))
    return all_jobs
