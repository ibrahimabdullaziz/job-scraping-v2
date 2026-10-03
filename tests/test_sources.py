"""
Tests for source adapters using saved sample HTML fixtures.

Each adapter test injects a local HTML fixture via the http_getter parameter
so no real network calls are made.
"""

from __future__ import annotations

import pytest
from models import Job
from sources.wuzzuf import fetch_wuzzuf, _parse_cards
from sources.linkedin import fetch_linkedin, _parse_cards as li_parse_cards


# ─── WUZZUF fixtures ──────────────────────────────────────────────────────────

WUZZUF_SAMPLE_HTML = """
<div>
  <a href="/jobs/p/abc123-backend-engineer-acme-corp" class="css-o171kl">Backend Engineer</a>
  <a href="/jobs/careers/acme-corp" class="css-company">Acme Corp</a>
  <span>Full Time</span><span>On-site</span>
</div>
<div>
  <a href="/jobs/p/xyz789-data-analyst-betasoft" class="css-o171kl">Data Analyst</a><a href="/jobs/careers/betasoft" class="css-company">BetaSoft</a><span>Full Time</span><span>Remote</span>
</div>
"""


class TestWuzzufParser:
    def test_parses_job_titles(self):
        jobs = _parse_cards(WUZZUF_SAMPLE_HTML)
        titles = [j.title for j in jobs]
        assert "Backend Engineer" in titles
        assert "Data Analyst" in titles

    def test_extracts_source_job_id(self):
        jobs = _parse_cards(WUZZUF_SAMPLE_HTML)
        ids = [j.source_job_id for j in jobs]
        assert "abc123-backend-engineer-acme-corp" in ids

    def test_builds_absolute_url(self):
        jobs = _parse_cards(WUZZUF_SAMPLE_HTML)
        for job in jobs:
            assert job.url.startswith("https://wuzzuf.net")

    def test_remote_flag_set(self):
        jobs = _parse_cards(WUZZUF_SAMPLE_HTML)
        remote_jobs = [j for j in jobs if j.is_remote]
        assert len(remote_jobs) >= 1

    def test_source_is_wuzzuf(self):
        jobs = _parse_cards(WUZZUF_SAMPLE_HTML)
        for job in jobs:
            assert job.source == "WUZZUF"

    def test_fetch_wuzzuf_with_injected_getter(self):
        def fake_get(url):
            return WUZZUF_SAMPLE_HTML

        jobs = fetch_wuzzuf(
            search_urls=["https://wuzzuf.net/a/Software-Engineering-Jobs"],
            http_getter=fake_get,
            request_delay=0,
        )
        assert len(jobs) >= 1

    def test_fetch_returns_empty_on_none_response(self):
        jobs = fetch_wuzzuf(
            search_urls=["https://wuzzuf.net/a/Fake"],
            http_getter=lambda url: None,
            request_delay=0,
        )
        assert jobs == []


# ─── LinkedIn fixtures ────────────────────────────────────────────────────────

LINKEDIN_SAMPLE_HTML = """
<ul>
  <li>
    <div>
      <h3 class="base-search-card__title">Senior DevOps Engineer</h3>
      <h4 class="base-search-card__subtitle">TechStartup</h4>
      <span class="job-search-card__location">Cairo, Egypt</span>
      <a href="https://www.linkedin.com/jobs/view/devops-engineer-12345678?refId=abc">Apply</a>
      <span class="job-search-card__job-insight">Full-time</span>
      <span class="job-search-card__job-insight">On-site</span>
    </div>
  </li>
  <li>
    <div>
      <h3 class="base-search-card__title">Machine Learning Engineer</h3>
      <h4 class="base-search-card__subtitle">AILab</h4>
      <span class="job-search-card__location">Remote</span>
      <a href="https://www.linkedin.com/jobs/view/ml-engineer-99887766">Apply</a>
      <span class="job-search-card__job-insight">Full-time</span>
      <span class="job-search-card__job-insight">Remote</span>
    </div>
  </li>
</ul>
"""


class TestLinkedInParser:
    def test_parses_job_titles(self):
        jobs = li_parse_cards(LINKEDIN_SAMPLE_HTML)
        titles = [j.title for j in jobs]
        assert "Senior DevOps Engineer" in titles
        assert "Machine Learning Engineer" in titles

    def test_extracts_numeric_job_id(self):
        jobs = li_parse_cards(LINKEDIN_SAMPLE_HTML)
        ids = [j.source_job_id for j in jobs]
        assert "12345678" in ids
        assert "99887766" in ids

    def test_remote_job_flagged(self):
        jobs = li_parse_cards(LINKEDIN_SAMPLE_HTML)
        ml_job = next(j for j in jobs if "Machine Learning" in j.title)
        assert ml_job.is_remote is True

    def test_onsite_job_not_remote(self):
        jobs = li_parse_cards(LINKEDIN_SAMPLE_HTML)
        devops_job = next(j for j in jobs if "DevOps" in j.title)
        assert devops_job.is_remote is False

    def test_source_is_linkedin(self):
        jobs = li_parse_cards(LINKEDIN_SAMPLE_HTML)
        for job in jobs:
            assert job.source == "LinkedIn"

    def test_fetch_linkedin_with_injected_getter(self):
        def fake_get(url):
            return LINKEDIN_SAMPLE_HTML

        jobs = fetch_linkedin(
            searches=[{"keywords": "devops", "location": "Egypt"}],
            http_getter=fake_get,
            request_delay=0,
        )
        assert len(jobs) >= 1

    def test_closed_job_excluded(self):
        closed_html = """
        <ul>
          <li>
            <div>
              <h3 class="base-search-card__title">Old Job</h3>
              No longer accepting applications
              <a href="https://www.linkedin.com/jobs/view/old-job-11111111">Apply</a>
            </div>
          </li>
        </ul>
        """
        jobs = li_parse_cards(closed_html)
        assert jobs == []
