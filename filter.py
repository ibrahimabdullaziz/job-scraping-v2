"""
Job filtering, location matching, and role classification.

Every raw Job from a source adapter passes through here before being
stored or posted.  The pipeline:

  1. check_location   — is the job in a target geography or remote?
  2. check_role       — does the title/tags match a tech role?
  3. check_excluded   — is the title explicitly excluded?

All three must pass.  Jobs with no apply URL are also dropped.
"""

from __future__ import annotations

import logging
import re

from models import Job
import config

log = logging.getLogger(__name__)


# ─── Pre-compiled patterns ────────────────────────────────────────────────────

_EGYPT_RE = re.compile(
    "|".join(re.escape(p) for p in config.EGYPT_PATTERNS),
    re.IGNORECASE,
)
_SAUDI_RE = re.compile(
    "|".join(re.escape(p) for p in config.SAUDI_PATTERNS),
    re.IGNORECASE,
)
_UAE_RE = re.compile(
    "|".join(re.escape(p) for p in config.UAE_PATTERNS),
    re.IGNORECASE,
)
_REMOTE_RE = re.compile(
    "|".join(re.escape(p) for p in config.REMOTE_MARKERS),
    re.IGNORECASE,
)

_EXCLUDED_RE = re.compile(
    "|".join(re.escape(p) for p in config.EXCLUDED_TITLE_PATTERNS),
    re.IGNORECASE,
)

_ROLE_RES: dict[str, re.Pattern] = {
    role: re.compile(
        "|".join(r"\b" + re.escape(kw) + r"\b" for kw in keywords),
        re.IGNORECASE,
    )
    for role, keywords in config.ROLE_KEYWORDS.items()
}


# ─── Individual checks ────────────────────────────────────────────────────────

def is_egypt(job: Job) -> bool:
    return bool(_EGYPT_RE.search(job.location))


def is_saudi(job: Job) -> bool:
    return bool(_SAUDI_RE.search(job.location))


def is_uae(job: Job) -> bool:
    return bool(_UAE_RE.search(job.location))


def is_remote(job: Job) -> bool:
    """True when the listing is clearly remote and not restricted to a non-target region.

    Remote jobs that explicitly restrict candidates to Americas/Europe/APAC
    (with no Worldwide/MENA mention) are treated as non-remote for our purposes
    so they don't slip through the geography gate.
    """
    if not (job.is_remote or bool(_REMOTE_RE.search(
            f"{job.location} {job.work_arrangement} {' '.join(str(t) for t in job.tags)}"
    ))):
        return False

    # --- Region gating for remote jobs ---
    region_text = job.location.lower()
    if region_text and region_text not in ("remote", "worldwide remote", ""):
        allow = any(tok in region_text for tok in config.REMOTE_REGION_ALLOWLIST)
        if allow:
            return True
        block = any(tok in region_text for tok in config.REMOTE_REGION_BLOCKLIST)
        if block:
            log.debug("DROP remote-region: %s @ %s — %s", job.title, job.company, job.location)
            return False

    return True


def is_target_geography(job: Job) -> bool:
    """Return True for jobs in Egypt, Saudi, UAE, or eligible Remote listings."""
    if is_egypt(job) or is_saudi(job) or is_uae(job):
        return True
    if is_remote(job):
        # Exclude remote listings that explicitly name a non-target country restriction
        # (best-effort; we can't fully parse restrictions from a public card)
        return True
    return False


def classify_role(job: Job) -> list[str]:
    """
    Return a list of role topic keys that match this job.

    Role classification evaluates title and tags only. Company name is excluded
    to prevent false matches (e.g. 'Backend Developer' at 'Orange Mobile').

    If a specific role topic (backend / frontend / mobile) matches, the generic
    'swe' topic is suppressed to avoid cross-posting the same job everywhere.
    """
    searchable = f"{job.title} {' '.join(str(t) for t in job.tags)}"
    matched = [
        role
        for role, pattern in _ROLE_RES.items()
        if pattern.search(searchable)
    ]
    # Suppress generic 'swe' when a specific role already covers this job.
    if "swe" in matched and any(r in matched for r in config.SPECIFIC_ROLE_TOPICS):
        matched = [r for r in matched if r != "swe"]
    return matched


def is_excluded(job: Job) -> bool:
    return bool(_EXCLUDED_RE.search(job.title))


def is_company_muted(job: Job) -> bool:
    """Return True if the job's company matches any muted company name."""
    if not job.company:
        return False
    company_lower = job.company.lower()
    for muted in config.get_muted_companies():
        if muted in company_lower:
            return True
    return False


def has_apply_url(job: Job) -> bool:
    return bool(job.url and job.url.startswith("http"))


# ─── Main pipeline ────────────────────────────────────────────────────────────

def should_include(job: Job) -> bool:
    """
    Return True if this job should be included in the feed.

    Logs a short reason when a job is dropped.
    """
    if not has_apply_url(job):
        log.debug("DROP no-url: %s @ %s", job.title, job.company)
        return False
    if is_company_muted(job):
        log.debug("DROP muted-company: %s @ %s", job.title, job.company)
        return False
    if is_excluded(job):
        log.debug("DROP excluded-title: %s", job.title)
        return False
    if not is_target_geography(job):
        log.debug("DROP geography: %s @ %s — %s", job.title, job.company, job.location)
        return False
    roles = classify_role(job)
    if not roles:
        log.debug("DROP no-role: %s @ %s", job.title, job.company)
        return False
    return True


def route_job(job: Job) -> list[str]:
    """
    Return the list of Telegram topic keys this job should be posted to.

    A job is always posted to its role topic(s).
    Additionally:
      - Egypt jobs → "egypt" topic.
      - Remote jobs → "remote" topic.
    """
    topics: list[str] = classify_role(job)

    if is_egypt(job):
        if "egypt" not in topics:
            topics.append("egypt")

    if is_remote(job):
        if "remote" not in topics:
            topics.append("remote")

    return topics
