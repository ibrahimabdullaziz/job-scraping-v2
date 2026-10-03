"""
Source registry.

Add or remove fetchers from ALL_FETCHERS to enable/disable sources without
touching the worker. Each entry is a (name, callable) pair where the callable
returns list[Job].
"""

from __future__ import annotations

from sources.wuzzuf import fetch_wuzzuf
from sources.linkedin import fetch_linkedin
from sources.remotive import fetch_remotive

# ─── Active sources ───────────────────────────────────────────────────────────
# To disable a source: comment it out or remove it.
# Source name appears in logs and job cards.
ALL_FETCHERS = [
    ("WUZZUF", fetch_wuzzuf),
    ("LinkedIn", fetch_linkedin),
    ("Remotive", fetch_remotive),
]
