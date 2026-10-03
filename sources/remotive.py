"""
Remotive source adapter (Remote tech jobs API).

Fetches listings from the public Remotive API:
  https://remotive.com/api/remote-jobs

All listings from Remotive are remote by definition. Each job is tagged with
is_remote=True and work_arrangement="Remote", so it routes directly to the
remote topic and its corresponding role topics.
"""

from __future__ import annotations

import html
import logging
from typing import Callable, Optional

from models import Job
from sources.http_utils import get_json

log = logging.getLogger(__name__)

REMOTIVE_API_URL = "https://remotive.com/api/remote-jobs"


def fetch_remotive(
    api_url: str = REMOTIVE_API_URL,
    http_getter: Optional[Callable[[str], Optional[dict | list]]] = None,
) -> list[Job]:
    """
    Fetch active remote listings from the Remotive public API.

    Returns a list of normalised Job instances.
    """
    getter = http_getter or get_json
    log.info("Remotive fetching: %s", api_url)

    data = getter(api_url)
    if not data or not isinstance(data, dict):
        log.warning("Remotive: invalid or empty response from API")
        return []

    raw_jobs = data.get("jobs", [])
    if not isinstance(raw_jobs, list):
        log.warning("Remotive: 'jobs' field is not a list")
        return []

    jobs: list[Job] = []
    for item in raw_jobs:
        job = _parse_remotive_job(item)
        if job:
            jobs.append(job)

    log.info("Remotive total fetched: %d jobs", len(jobs))
    return jobs


def _parse_remotive_job(item: dict) -> Optional[Job]:
    """Convert a single Remotive job dict into a normalised Job model."""
    if not isinstance(item, dict):
        return None

    title = html.unescape(item.get("title", "")).strip()
    if not title:
        return None

    company = html.unescape(item.get("company_name", "")).strip()
    url = item.get("url", "").strip()
    if not url:
        return None

    job_id = str(item.get("id", "")).strip()
    category = item.get("category", "")

    # Location candidate requirement (e.g. "Worldwide", "Europe", "USA")
    candidate_location = item.get("candidate_required_location", "").strip()
    location = candidate_location or "Worldwide Remote"

    # Extract tags
    raw_tags = item.get("tags") or []
    tags = [str(t).strip() for t in raw_tags if str(t).strip()]
    if category and category not in tags:
        tags.append(category)

    # Clean publication date
    pub_date = item.get("publication_date", "")

    return Job(
        title=title,
        company=company,
        location=location,
        url=url,
        source="Remotive",
        source_job_id=job_id,
        job_type=item.get("job_type", "") or "",
        salary=item.get("salary", "") or "",
        is_remote=True,
        work_arrangement="Remote",
        seniority="",
        tags=tags,
    )
