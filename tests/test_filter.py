"""
Unit tests: location/remote matching, role classification, exclusions,
URL normalisation, and deduplication.
"""

from __future__ import annotations

import pytest
from models import Job
import filter as job_filter


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_job(**kw) -> Job:
    defaults = dict(
        title="Software Engineer",
        company="Acme",
        location="Cairo, Egypt",
        url="https://example.com/jobs/123",
        source="WUZZUF",
    )
    defaults.update(kw)
    return Job(**defaults)


# ─── Geography matching ───────────────────────────────────────────────────────

class TestLocationMatching:
    def test_egypt_cairo(self):
        assert job_filter.is_egypt(make_job(location="Cairo, Egypt"))

    def test_egypt_arabic(self):
        assert job_filter.is_egypt(make_job(location="القاهرة"))

    def test_egypt_giza(self):
        assert job_filter.is_egypt(make_job(location="Giza Governorate, Egypt"))

    def test_saudi_riyadh(self):
        assert job_filter.is_saudi(make_job(location="Riyadh, Saudi Arabia"))

    def test_saudi_ksa(self):
        assert job_filter.is_saudi(make_job(location="KSA"))

    def test_uae_dubai(self):
        assert job_filter.is_uae(make_job(location="Dubai, United Arab Emirates"))

    def test_uae_abbreviation(self):
        assert job_filter.is_uae(make_job(location="UAE"))

    def test_no_match_other_country(self):
        job = make_job(location="London, UK")
        assert not job_filter.is_egypt(job)
        assert not job_filter.is_saudi(job)
        assert not job_filter.is_uae(job)

    def test_is_target_egypt(self):
        assert job_filter.is_target_geography(make_job(location="Cairo, Egypt"))

    def test_is_target_saudi(self):
        assert job_filter.is_target_geography(make_job(location="Jeddah, Saudi Arabia"))

    def test_is_target_uae(self):
        assert job_filter.is_target_geography(make_job(location="Abu Dhabi, UAE"))

    def test_non_target_excluded(self):
        assert not job_filter.is_target_geography(make_job(location="Berlin, Germany"))


class TestRemoteMatching:
    def test_remote_flag(self):
        assert job_filter.is_remote(make_job(is_remote=True, location="Anywhere"))

    def test_remote_in_location(self):
        assert job_filter.is_remote(make_job(location="Remote"))

    def test_remote_work_arrangement(self):
        assert job_filter.is_remote(make_job(work_arrangement="Remote", location="Cairo"))

    def test_wfh_marker(self):
        assert job_filter.is_remote(make_job(location="Work From Home"))

    def test_hybrid_is_not_remote(self):
        assert not job_filter.is_remote(make_job(work_arrangement="Hybrid", location="Cairo, Egypt"))

    def test_onsite_is_not_remote(self):
        assert not job_filter.is_remote(make_job(work_arrangement="On-site", location="Riyadh"))

    def test_remote_is_target_geo(self):
        job = make_job(location="Anywhere", is_remote=True)
        assert job_filter.is_target_geography(job)


# ─── Role classification ──────────────────────────────────────────────────────

class TestRoleClassification:
    def test_swe_backend(self):
        # 'backend' is a specific role — 'swe' must be suppressed to avoid spam.
        roles = job_filter.classify_role(make_job(title="Backend Developer"))
        assert "backend" in roles
        assert "swe" not in roles, "swe must not duplicate a specific backend role"

    def test_swe_suppressed_for_frontend(self):
        roles = job_filter.classify_role(make_job(title="React Frontend Engineer"))
        assert "frontend" in roles
        assert "swe" not in roles, "swe must not duplicate a specific frontend role"

    def test_swe_suppressed_for_mobile(self):
        roles = job_filter.classify_role(make_job(title="Flutter Mobile Developer"))
        assert "mobile" in roles
        assert "swe" not in roles, "swe must not duplicate a specific mobile role"

    def test_frontend(self):
        roles = job_filter.classify_role(make_job(title="React Frontend Engineer"))
        assert "frontend" in roles

    def test_mobile(self):
        roles = job_filter.classify_role(make_job(title="iOS Flutter Developer"))
        assert "mobile" in roles

    def test_swe_fullstack(self):
        roles = job_filter.classify_role(make_job(title="Full Stack Engineer"))
        assert "swe" in roles

    def test_qa(self):
        roles = job_filter.classify_role(make_job(title="QA Engineer"))
        assert "qa" in roles

    def test_devops(self):
        roles = job_filter.classify_role(make_job(title="DevOps Engineer"))
        assert "devops" in roles

    def test_cloud(self):
        roles = job_filter.classify_role(make_job(title="Cloud Architect"))
        assert "devops" in roles

    def test_cybersecurity(self):
        roles = job_filter.classify_role(make_job(title="Cybersecurity Analyst"))
        assert "cybersecurity" in roles

    def test_data_ai(self):
        roles = job_filter.classify_role(make_job(title="Machine Learning Engineer"))
        assert "data_ai" in roles

    def test_data_engineer(self):
        roles = job_filter.classify_role(make_job(title="Data Engineer"))
        assert "data_ai" in roles

    def test_it_support(self):
        roles = job_filter.classify_role(make_job(title="IT Support Specialist"))
        assert "it_support" in roles

    def test_product_manager(self):
        roles = job_filter.classify_role(make_job(title="Product Manager"))
        assert "product" in roles

    def test_design(self):
        roles = job_filter.classify_role(make_job(title="UI/UX Designer"))
        assert "design" in roles

    def test_no_role_match(self):
        roles = job_filter.classify_role(make_job(title="Chef"))
        assert roles == []

    def test_dual_role(self):
        """A Data Engineer job may legitimately match both swe and data_ai."""
        roles = job_filter.classify_role(make_job(title="Backend Data Engineer"))
        assert "data_ai" in roles


# ─── Exclusion rules ──────────────────────────────────────────────────────────

class TestExclusions:
    def test_excluded_sales(self):
        assert job_filter.is_excluded(make_job(title="Sales Representative"))

    def test_excluded_customer_service(self):
        assert job_filter.is_excluded(make_job(title="Customer Service Agent"))

    def test_excluded_accountant(self):
        assert job_filter.is_excluded(make_job(title="Senior Accountant"))

    def test_not_excluded_software(self):
        assert not job_filter.is_excluded(make_job(title="Software Engineer"))

    def test_not_excluded_data(self):
        assert not job_filter.is_excluded(make_job(title="Data Analyst"))

    def test_no_url_dropped(self):
        job = make_job(url="")
        assert not job_filter.should_include(job)

    def test_excluded_title_dropped(self):
        job = make_job(title="Telesales Agent")
        assert not job_filter.should_include(job)

    def test_wrong_geo_dropped(self):
        job = make_job(title="Software Engineer", location="London, UK")
        assert not job_filter.should_include(job)

    def test_good_job_included(self):
        job = make_job(title="Software Engineer", location="Cairo, Egypt")
        assert job_filter.should_include(job)

    def test_muted_company_dropped(self, monkeypatch):
        monkeypatch.setenv("MUTED_COMPANIES", "micro1, Hire Feed , Jobs AI")
        job = make_job(company="micro1 AI Inc")
        assert job_filter.is_company_muted(job)
        assert not job_filter.should_include(job)

    def test_unmuted_company_allowed(self, monkeypatch):
        monkeypatch.setenv("MUTED_COMPANIES", "micro1, Hire Feed")
        job = make_job(company="Google")
        assert not job_filter.is_company_muted(job)
        assert job_filter.should_include(job)


# ─── Topic routing ────────────────────────────────────────────────────────────

class TestRouting:
    def test_egypt_backend_gets_egypt_topic(self):
        job = make_job(title="Backend Developer", location="Cairo, Egypt")
        topics = job_filter.route_job(job)
        assert "egypt" in topics
        assert "backend" in topics
        assert "swe" not in topics  # swe is suppressed when a specific role matches

    def test_remote_gets_remote_topic(self):
        job = make_job(title="Software Engineer", location="Remote", is_remote=True)
        topics = job_filter.route_job(job)
        assert "remote" in topics
        assert "swe" in topics

    def test_saudi_no_egypt_topic(self):
        job = make_job(title="DevOps Engineer", location="Riyadh, Saudi Arabia")
        topics = job_filter.route_job(job)
        assert "egypt" not in topics
        assert "devops" in topics

    def test_uae_no_egypt_topic(self):
        job = make_job(title="Data Analyst", location="Dubai, UAE")
        topics = job_filter.route_job(job)
        assert "egypt" not in topics
        assert "data_ai" in topics

    def test_egypt_remote_gets_both_topics(self):
        """A Cairo job listed as Remote gets both egypt and remote topics."""
        job = make_job(
            title="Backend Developer",
            location="Cairo, Egypt",
            work_arrangement="Remote",
            is_remote=True,
        )
        topics = job_filter.route_job(job)
        assert "egypt" in topics
        assert "remote" in topics
        assert "backend" in topics
        assert "swe" not in topics  # swe suppressed because backend matched


# ─── URL normalisation / deduplication ───────────────────────────────────────

class TestDeduplication:
    def test_same_job_same_hash(self):
        j1 = make_job(url="https://example.com/jobs/123")
        j2 = make_job(url="https://example.com/jobs/123")
        assert j1.content_hash == j2.content_hash

    def test_utm_stripped_from_canonical(self):
        j = make_job(url="https://example.com/jobs/123?utm_source=linkedin&utm_campaign=spring")
        assert "utm" not in j.canonical_url

    def test_trailing_slash_stripped(self):
        j1 = make_job(url="https://example.com/jobs/123")
        j2 = make_job(url="https://example.com/jobs/123/")
        assert j1.canonical_url == j2.canonical_url

    def test_source_job_id_dedup_key(self):
        j = make_job(source="LinkedIn", source_job_id="987654321")
        assert j.dedup_key == "linkedin:987654321"

    def test_url_dedup_key_when_no_source_id(self):
        j = make_job(source="WUZZUF", source_job_id="", url="https://wuzzuf.net/jobs/p/abc")
        assert j.dedup_key.startswith("url:")

    def test_different_titles_different_hashes(self):
        j1 = make_job(title="Backend Engineer")
        j2 = make_job(title="Frontend Engineer")
        assert j1.content_hash != j2.content_hash
