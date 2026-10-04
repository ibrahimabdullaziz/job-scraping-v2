"""
Centralised configuration for the Egypt/Gulf tech-jobs Telegram bot.

All secrets come from environment variables; no credentials are stored here.
Disable a source or topic by removing its env-var or setting it to an empty
string — the worker will skip it without code changes.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

# ─── Bot credentials (required) ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_GROUP_ID: str = os.environ.get("TELEGRAM_GROUP_ID", "")

# ─── Worker timing ───────────────────────────────────────────────────────────
FETCH_INTERVAL_SECONDS: int = int(os.environ.get("FETCH_INTERVAL_SECONDS", "600"))  # 10 min
TELEGRAM_SEND_DELAY: float = float(os.environ.get("TELEGRAM_SEND_DELAY", "2.5"))

# ─── Database path ───────────────────────────────────────────────────────────
DB_PATH: str = os.environ.get("DB_PATH", "jobs.db")

# ─── Seed mode: save jobs to DB without posting them ────────────────────────
SEED_MODE: bool = os.environ.get("SEED_MODE", "").lower() in ("1", "true", "yes")

# ─── Muted companies: drop jobs from unwanted or spam employers ────────────
def get_muted_companies() -> list[str]:
    """Return a list of lowercase company names to exclude."""
    raw = os.environ.get("MUTED_COMPANIES", "")
    return [c.strip().lower() for c in raw.split(",") if c.strip()]

MUTED_COMPANIES: list[str] = get_muted_companies()

# ─── Topic thread-ID mapping ─────────────────────────────────────────────────
# Each key maps to an env-var holding the Telegram thread ID (integer string).
# If the env-var is missing or empty, that topic is disabled automatically.
TOPIC_ENV_VARS: dict[str, str] = {
    "swe":           "TOPIC_SWE",
    "backend":       "TOPIC_BACKEND",
    "frontend":      "TOPIC_FRONTEND",
    "mobile":        "TOPIC_MOBILE",
    "qa":            "TOPIC_QA",
    "devops":        "TOPIC_DEVOPS",
    "cybersecurity": "TOPIC_CYBERSECURITY",
    "data_ai":       "TOPIC_DATA_AI",
    "it_support":    "TOPIC_IT_SUPPORT",
    "product":       "TOPIC_PRODUCT",
    "design":        "TOPIC_DESIGN",
    "egypt":         "TOPIC_EGYPT",
    "remote":        "TOPIC_REMOTE",
}


def get_topic_thread_id(topic_key: str) -> Optional[int]:
    """Return the Telegram thread ID for *topic_key*, or None if not configured."""
    env_name = TOPIC_ENV_VARS.get(topic_key)
    if not env_name:
        return None
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        log.warning("Topic %s env-var %s=%r is not an integer — topic disabled", topic_key, env_name, raw)
        return None


def enabled_topics() -> list[str]:
    """Return topic keys that have a valid thread ID configured."""
    return [k for k in TOPIC_ENV_VARS if get_topic_thread_id(k) is not None]


# ─── Channel metadata (display names) ────────────────────────────────────────
TOPIC_META: dict[str, dict] = {
    "swe":           {"label": "💻 Software Engineering"},
    "qa":            {"label": "🧪 QA & Testing"},
    "devops":        {"label": "☁️ DevOps / Cloud"},
    "cybersecurity": {"label": "🔒 Cybersecurity"},
    "data_ai":       {"label": "📊 Data / AI"},
    "it_support":    {"label": "🖥️ IT Support"},
    "product":       {"label": "📋 Product"},
    "design":        {"label": "🎨 Design"},
    "egypt":         {"label": "🇪🇬 Egypt"},
    "remote":        {"label": "🌍 Remote"},
}

# ─── Geography matching ───────────────────────────────────────────────────────
EGYPT_PATTERNS: tuple[str, ...] = (
    "egypt", "cairo", "giza", "alexandria", "heliopolis", "maadi",
    "new cairo", "6th of october", "october city", "nasr city",
    "مصر", "القاهرة", "الجيزة", "الإسكندرية",
)

SAUDI_PATTERNS: tuple[str, ...] = (
    "saudi", "riyadh", "jeddah", "dammam", "khobar", "mecca", "medina",
    "saudi arabia", "ksa", "الرياض", "جدة", "المملكة العربية السعودية",
)

UAE_PATTERNS: tuple[str, ...] = (
    "uae", "dubai", "abu dhabi", "sharjah", "ajman", "united arab emirates",
    "دبي", "أبوظبي", "الإمارات",
)

REMOTE_MARKERS: tuple[str, ...] = (
    "remote", "work from home", "wfh", "fully remote",
    "عمل عن بُعد", "عمل من المنزل",
)

# Remote-job geography gating
# If a remote job's candidate location contains ANY allowlist token, it passes.
# If none of the allowlist tokens match AND at least one blocklist token matches,
# the job is dropped (it targets a region we don't serve).
REMOTE_REGION_ALLOWLIST: tuple[str, ...] = (
    "worldwide", "anywhere", "global",
    "egypt", "cairo", "mena", "middle east",
    "saudi", "uae", "gulf", "africa",
    "مصر", "الشرق الأوسط",
)
REMOTE_REGION_BLOCKLIST: tuple[str, ...] = (
    "usa", "united states", "canada", "america", "latam", "latin america",
    "europe", "uk", "united kingdom", "apac", "asia pacific",
    "australia", "new zealand", "singapore", "japan", "south korea",
    "northern america",
)

# ─── Role classification keywords ────────────────────────────────────────────
# Topics that are considered "specific" — if a job matches any of these,
# we do NOT also route it to the generic 'swe' topic to avoid spam.
SPECIFIC_ROLE_TOPICS: frozenset[str] = frozenset({"backend", "frontend", "mobile"})

ROLE_KEYWORDS: dict[str, list[str]] = {
    "swe": [
        # Generic software engineering titles only.
        # Backend/frontend/mobile-specific keywords live in their own topics
        # so we avoid double-posting the same job to multiple channels.
        "software engineer", "software developer",
        "full stack", "fullstack", "full-stack",
        "web developer", "web engineer",
        "java developer", "java engineer", "python developer",
        ".net developer", "node developer",
        "rails developer", "golang", "rust developer",
        "embedded", "firmware",
    ],
    "backend": [
        "backend", "back-end", "python developer", "python engineer",
        "java developer", "java engineer", ".net developer", ".net engineer",
        "dotnet", "c# developer", "node developer", "node.js", "nodejs",
        "django", "flask", "fastapi", "spring boot", "rails developer",
        "ruby on rails", "golang", "go developer", "rust developer",
        "php developer", "laravel", "c++ developer", "api developer",
        "backend developer", "backend engineer",
    ],
    "frontend": [
        "frontend", "front-end", "front end", "web developer",
        "react developer", "react engineer", "react.js", "vue developer",
        "vue.js", "angular developer", "angular engineer", "next.js",
        "nuxt", "javascript developer", "typescript developer",
        "frontend developer", "frontend engineer", "svelte", "html/css",
    ],
    "mobile": [
        "mobile developer", "mobile engineer", "ios developer", "ios engineer",
        "android developer", "android engineer", "flutter developer",
        "flutter engineer", "flutter", "react native", "swift developer",
        "kotlin developer", "mobile app",
    ],
    "qa": [
        "qa engineer", "qa analyst", "quality assurance", "quality engineer",
        "test engineer", "tester", "automation engineer", "software tester",
        "sdet", "test lead", "qa lead",
    ],
    "devops": [
        "devops", "dev ops", "cloud engineer", "cloud architect",
        "site reliability", "sre", "infrastructure engineer",
        "platform engineer", "kubernetes", "docker", "aws engineer",
        "azure engineer", "gcp engineer", "terraform", "ci/cd",
        "systems engineer", "linux administrator", "linux admin",
    ],
    "cybersecurity": [
        "cybersecurity", "cyber security", "information security",
        "infosec", "security engineer", "security analyst",
        "penetration tester", "pentester", "soc analyst",
        "network security", "security operations",
    ],
    "data_ai": [
        "data engineer", "data analyst", "data scientist", "data science",
        "machine learning", "ml engineer", "ai engineer", "artificial intelligence",
        "deep learning", "nlp engineer", "data warehouse", "etl developer",
        "bi developer", "business intelligence", "analytics engineer",
        "big data", "spark", "databricks", "snowflake",
    ],
    "it_support": [
        "it support", "technical support", "help desk", "helpdesk",
        "service desk", "desktop support", "system administrator",
        "sysadmin", "network administrator", "network engineer",
        "it administrator", "application support", "erp support",
        "odoo", "sap consultant", "salesforce", "noc engineer",
    ],
    "product": [
        "product manager", "product owner", "product analyst",
        "project manager", "scrum master", "program manager",
        "business analyst", "ba ", "product lead",
    ],
    "design": [
        "ui designer", "ux designer", "ui/ux", "ux/ui", "product designer",
        "graphic designer", "visual designer", "motion designer",
        "interaction designer", "web designer",
    ],
}

# Roles/titles to exclude entirely (non-tech, unrelated)
EXCLUDED_TITLE_PATTERNS: tuple[str, ...] = (
    "sales representative", "sales executive", "account manager",
    "customer service", "call center", "telesales",
    "accountant", "finance manager", "hr specialist", "recruiter",
    "legal counsel", "lawyer", "doctor", "physician", "nurse",
    "teacher", "instructor", "chef", "cook", "driver",
    "warehouse", "logistics", "supply chain",
)

# ─── LinkedIn fetch settings ─────────────────────────────────────────────────
LINKEDIN_FRESHNESS_SECONDS: int = int(os.environ.get("LINKEDIN_FRESHNESS_SECONDS", "3600"))
LINKEDIN_REQUEST_DELAY: float = float(os.environ.get("LINKEDIN_REQUEST_DELAY", "4.0"))
LINKEDIN_MAX_PAGES_PER_SEARCH: int = int(os.environ.get("LINKEDIN_MAX_PAGES_PER_SEARCH", "1"))

# ─── WUZZUF fetch settings ───────────────────────────────────────────────────
WUZZUF_MAX_PAGES_PER_SEARCH: int = int(os.environ.get("WUZZUF_MAX_PAGES_PER_SEARCH", "1"))
