"""
Job data model.

Each source adapter normalizes its output into a Job instance. The model
stores only public card-level information scraped from job listings.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# ─── Tracking-parameter keys stripped from canonical URLs ───────────────────
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "trk",
        "tracking_id",
        "ref",
        "refid",
    }
)


def _canonical_url(raw: str) -> str:
    """Strip tracking params and normalise URL for deduplication."""
    if not raw:
        return ""
    try:
        parts = urlsplit(raw.strip())
        qs = [
            (k, v)
            for k, v in parse_qsl(parts.query)
            if not any(k.startswith(p) for p in _TRACKING_PREFIXES)
            and k.lower() not in _TRACKING_KEYS
        ]
        cleaned = urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path.rstrip("/"),
                urlencode(qs),
                "",  # drop fragment
            )
        )
        return cleaned
    except Exception:
        return raw


@dataclass
class Job:
    """Normalised job record produced by every source adapter."""

    title: str
    company: str
    location: str
    url: str
    source: str
    source_job_id: str = ""  # source-native ID (e.g. LinkedIn numeric ID)
    salary: str = ""
    job_type: str = ""  # Full Time / Part Time / Internship / …
    work_arrangement: str = ""  # On-site / Hybrid / Remote
    seniority: str = ""  # Junior / Mid / Senior / Lead / … (best-effort)
    tags: list = field(default_factory=list)
    is_remote: bool = False
    first_seen_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ── derived properties ──────────────────────────────────────────────────

    @property
    def canonical_url(self) -> str:
        return _canonical_url(self.url)

    @property
    def content_hash(self) -> str:
        """Stable fingerprint used as the unique DB key."""
        parts = "|".join(
            [
                self.source.lower(),
                self.source_job_id,
                self.canonical_url,
                self.title.lower().strip(),
                self.company.lower().strip(),
                self.location.lower().strip(),
            ]
        )
        return hashlib.sha256(parts.encode()).hexdigest()

    @property
    def dedup_key(self) -> str:
        """
        Primary dedup key.

        Priority:
          1. source_job_id when provided.
          2. canonical_url otherwise.
          3. Fallback: normalised title+company+location hash.
        """
        if self.source_job_id:
            return f"{self.source.lower()}:{self.source_job_id}"
        if self.canonical_url:
            return f"url:{self.canonical_url}"
        norm = f"{self.title.lower().strip()}|{self.company.lower().strip()}|{self.location.lower().strip()}"
        return f"norm:{hashlib.md5(norm.encode()).hexdigest()}"

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "source": self.source,
            "source_job_id": self.source_job_id,
            "salary": self.salary,
            "job_type": self.job_type,
            "work_arrangement": self.work_arrangement,
            "seniority": self.seniority,
            "tags": self.tags,
            "is_remote": self.is_remote,
            "first_seen_at": self.first_seen_at,
            "canonical_url": self.canonical_url,
            "content_hash": self.content_hash,
        }
