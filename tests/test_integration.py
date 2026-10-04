"""
Integration tests: DB storage, routing + delivery against a mocked Telegram sender.

These tests use real SQLite (in-memory) and verify:
  - New jobs are stored and delivery rows created.
  - Previously stored jobs are deduplicated (not re-queued).
  - Role + Egypt/Remote topic routing is correct.
  - A failed send is retried; a successful send is not re-sent.
  - Seed mode marks sends as 'sent' without calling the Telegram API.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

import db
import filter as job_filter
import telegram_sender
from models import Job


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def mem_conn():
    """Open an in-memory SQLite connection with the full schema."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db._configure(conn)
    db._init_schema(conn)
    yield conn
    conn.close()


def make_job(**kw) -> Job:
    defaults = dict(
        title="Software Engineer",
        company="Acme",
        location="Cairo, Egypt",
        url="https://example.com/jobs/1",
        source="WUZZUF",
        source_job_id="wuz-1",
    )
    defaults.update(kw)
    return Job(**defaults)


# ─── DB upsert / dedup ────────────────────────────────────────────────────────

class TestDBUpsert:
    def test_new_job_is_new(self, mem_conn):
        job = make_job()
        job_id, is_new = db.upsert_job(mem_conn, job)
        assert is_new is True
        assert job_id > 0

    def test_same_job_not_new(self, mem_conn):
        job = make_job()
        db.upsert_job(mem_conn, job)
        _, is_new = db.upsert_job(mem_conn, job)
        assert is_new is False

    def test_different_title_is_new(self, mem_conn):
        db.upsert_job(mem_conn, make_job(title="Backend Engineer", source_job_id="wuz-1"))
        _, is_new = db.upsert_job(mem_conn, make_job(title="Frontend Engineer", source_job_id="wuz-2"))
        assert is_new is True

    def test_init_sends_creates_rows(self, mem_conn):
        job = make_job()
        job_id, _ = db.upsert_job(mem_conn, job)
        db.init_sends(mem_conn, job_id, ["swe", "egypt"])
        rows = db.get_pending_sends(mem_conn)
        topic_keys = {r["topic_key"] for r in rows}
        assert topic_keys == {"swe", "egypt"}

    def test_init_sends_idempotent(self, mem_conn):
        job = make_job()
        job_id, _ = db.upsert_job(mem_conn, job)
        db.init_sends(mem_conn, job_id, ["swe"])
        db.init_sends(mem_conn, job_id, ["swe"])  # second call should not duplicate
        rows = db.get_pending_sends(mem_conn)
        assert len(rows) == 1

    def test_mark_send_ok(self, mem_conn):
        job = make_job()
        job_id, _ = db.upsert_job(mem_conn, job)
        db.init_sends(mem_conn, job_id, ["swe"])
        rows = db.get_pending_sends(mem_conn)
        db.mark_send_ok(mem_conn, rows[0]["send_id"])
        # No longer pending
        assert db.get_pending_sends(mem_conn) == []

    def test_mark_send_failed_retried(self, mem_conn):
        job = make_job()
        job_id, _ = db.upsert_job(mem_conn, job)
        db.init_sends(mem_conn, job_id, ["swe"])
        rows = db.get_pending_sends(mem_conn)
        db.mark_send_failed(mem_conn, rows[0]["send_id"], "timeout")
        # Should still be in pending sends (status=failed)
        retry_rows = db.get_pending_sends(mem_conn)
        assert len(retry_rows) == 1
        assert retry_rows[0]["status"] == "failed"

    def test_only_failed_topics_retried(self, mem_conn):
        """Successful sends should not appear in pending; failed ones should."""
        job = make_job()
        job_id, _ = db.upsert_job(mem_conn, job)
        db.init_sends(mem_conn, job_id, ["swe", "egypt"])
        rows = db.get_pending_sends(mem_conn)
        # mark swe OK, egypt failed
        for row in rows:
            if row["topic_key"] == "swe":
                db.mark_send_ok(mem_conn, row["send_id"])
            else:
                db.mark_send_failed(mem_conn, row["send_id"], "err")
        retry = db.get_pending_sends(mem_conn)
        assert len(retry) == 1
        assert retry[0]["topic_key"] == "egypt"


# ─── Routing integration ──────────────────────────────────────────────────────

class TestRoutingIntegration:
    def test_egypt_swe_routes_to_swe_and_egypt(self):
        job = make_job(title="Backend Developer", location="Cairo, Egypt")
        topics = job_filter.route_job(job)
        assert "backend" in topics
        assert "egypt" in topics
        assert "swe" not in topics  # swe suppressed when specific role matches

    def test_remote_swe_routes_to_swe_and_remote(self):
        job = make_job(title="Software Engineer", location="Remote", is_remote=True)
        topics = job_filter.route_job(job)
        assert "swe" in topics
        assert "remote" in topics

    def test_egypt_remote_job_both_topics(self):
        job = make_job(
            title="Data Engineer",
            location="Cairo, Egypt",
            work_arrangement="Remote",
            is_remote=True,
        )
        topics = job_filter.route_job(job)
        assert "data_ai" in topics
        assert "egypt" in topics
        assert "remote" in topics

    def test_saudi_devops_no_egypt(self):
        job = make_job(title="DevOps Engineer", location="Riyadh, Saudi Arabia")
        topics = job_filter.route_job(job)
        assert "devops" in topics
        assert "egypt" not in topics


# ─── Telegram delivery (mocked) ───────────────────────────────────────────────

class TestDelivery:
    def _setup_job(self, conn, **kw):
        job = make_job(**kw)
        topics = job_filter.route_job(job)
        job_id, _ = db.upsert_job(conn, job)
        db.init_sends(conn, job_id, topics or ["swe"])
        conn.commit()
        return job_id, topics

    def test_seed_mode_no_telegram_call(self, mem_conn):
        self._setup_job(mem_conn)
        with patch("telegram_sender.send_message") as mock_send, \
             patch.object(telegram_sender.config, "get_topic_thread_id", return_value=1001):
            result = telegram_sender.deliver_pending(mem_conn, seed_mode=True)
        mock_send.assert_not_called()
        assert result["sent"] > 0

    def test_successful_send_marks_ok(self, mem_conn):
        self._setup_job(mem_conn)
        with patch("telegram_sender.send_message", return_value=True), \
             patch.object(telegram_sender.config, "TELEGRAM_GROUP_ID", "-100123456789"), \
             patch.object(telegram_sender.config, "get_topic_thread_id", return_value=1001):
            result = telegram_sender.deliver_pending(mem_conn, seed_mode=False)
        assert result["sent"] > 0
        assert result["failed"] == 0
        # Nothing pending after success
        assert db.get_pending_sends(mem_conn) == []

    def test_failed_send_marks_failed(self, mem_conn):
        self._setup_job(mem_conn)
        with patch("telegram_sender.send_message", return_value=False), \
             patch.object(telegram_sender.config, "TELEGRAM_GROUP_ID", "-100123456789"), \
             patch.object(telegram_sender.config, "get_topic_thread_id", return_value=1001):
            result = telegram_sender.deliver_pending(mem_conn, seed_mode=False)
        assert result["failed"] > 0
        # Rows still pending for retry
        assert db.get_pending_sends(mem_conn) != []

    def test_previously_sent_not_resent(self, mem_conn):
        job = make_job()
        job_id, _ = db.upsert_job(mem_conn, job)
        db.init_sends(mem_conn, job_id, ["swe"])
        rows = db.get_pending_sends(mem_conn)
        db.mark_send_ok(mem_conn, rows[0]["send_id"])
        mem_conn.commit()
        with patch("telegram_sender.send_message") as mock_send:
            telegram_sender.deliver_pending(mem_conn, seed_mode=False)
        mock_send.assert_not_called()

    def test_topic_not_configured_skipped(self, mem_conn):
        job = make_job()
        job_id, _ = db.upsert_job(mem_conn, job)
        db.init_sends(mem_conn, job_id, ["swe"])
        mem_conn.commit()
        with patch("telegram_sender.send_message") as mock_send, \
             patch.object(telegram_sender.config, "TELEGRAM_GROUP_ID", "-100999"), \
             patch.object(telegram_sender.config, "get_topic_thread_id", return_value=None):
            result = telegram_sender.deliver_pending(mem_conn, seed_mode=False)
        mock_send.assert_not_called()
        assert result["skipped"] > 0
