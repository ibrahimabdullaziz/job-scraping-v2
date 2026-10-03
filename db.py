
"""
SQLite persistence layer.

Schema:
  - jobs          : one row per unique job (keyed by content_hash).
  - job_sends     : one row per (job_id, topic_key) delivery attempt.
                    Allows per-topic retry without re-sending already-sent topics.
  - metadata      : key/value store for worker bookkeeping.

WAL mode is enabled for Railway (concurrent read + write are fine there).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from models import Job

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2


@contextmanager
def connect(db_path: str | Path = "jobs.db") -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection, ensure schema exists, and auto-commit/rollback."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        _configure(conn)
        _init_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _configure(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 10000")


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            source           TEXT    NOT NULL,
            source_job_id    TEXT    NOT NULL DEFAULT '',
            title            TEXT    NOT NULL,
            company          TEXT    NOT NULL DEFAULT '',
            location         TEXT    NOT NULL DEFAULT '',
            url              TEXT    NOT NULL,
            canonical_url    TEXT    NOT NULL,
            salary           TEXT    NOT NULL DEFAULT '',
            job_type         TEXT    NOT NULL DEFAULT '',
            work_arrangement TEXT    NOT NULL DEFAULT '',
            seniority        TEXT    NOT NULL DEFAULT '',
            tags_json        TEXT    NOT NULL DEFAULT '[]',
            is_remote        INTEGER NOT NULL DEFAULT 0,
            content_hash     TEXT    NOT NULL UNIQUE,
            first_seen_at    TEXT    NOT NULL,
            last_seen_at     TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_content_hash
            ON jobs(content_hash);

        CREATE INDEX IF NOT EXISTS idx_jobs_source_job_id
            ON jobs(source, source_job_id)
            WHERE source_job_id != '';

        CREATE INDEX IF NOT EXISTS idx_jobs_canonical_url
            ON jobs(canonical_url);

        CREATE TABLE IF NOT EXISTS job_sends (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id     INTEGER NOT NULL,
            topic_key  TEXT    NOT NULL,
            status     TEXT    NOT NULL DEFAULT 'pending',
            sent_at    TEXT,
            error      TEXT    NOT NULL DEFAULT '',
            updated_at TEXT    NOT NULL,
            UNIQUE(job_id, topic_key),
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_job_sends_status
            ON job_sends(status, updated_at);
        """
    )


# ─── Public API ──────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_job(conn: sqlite3.Connection, job: Job) -> tuple[int, bool]:
    """
    Insert or update a job row.

    Returns (row_id, is_new):
      is_new=True  → first time this job was seen (should be posted).
      is_new=False → already in DB (deduplicated).
    """
    now = _now()
    cur = conn.execute(
        "SELECT id FROM jobs WHERE content_hash = ?",
        (job.content_hash,),
    )
    row = cur.fetchone()
    if row:
        conn.execute(
            "UPDATE jobs SET last_seen_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        return row["id"], False

    cur = conn.execute(
        """
        INSERT INTO jobs
            (source, source_job_id, title, company, location, url, canonical_url,
             salary, job_type, work_arrangement, seniority, tags_json, is_remote,
             content_hash, first_seen_at, last_seen_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            job.source,
            job.source_job_id,
            job.title,
            job.company,
            job.location,
            job.url,
            job.canonical_url,
            job.salary,
            job.job_type,
            job.work_arrangement,
            job.seniority,
            json.dumps(job.tags),
            int(job.is_remote),
            job.content_hash,
            job.first_seen_at,
            now,
        ),
    )
    return cur.lastrowid, True


def init_sends(conn: sqlite3.Connection, job_id: int, topics: list[str]) -> None:
    """Create pending send rows for each topic that doesn't already have a row."""
    now = _now()
    for topic_key in topics:
        conn.execute(
            """
            INSERT OR IGNORE INTO job_sends (job_id, topic_key, status, updated_at)
            VALUES (?, ?, 'pending', ?)
            """,
            (job_id, topic_key, now),
        )


def get_pending_sends(
    conn: sqlite3.Connection,
) -> list[sqlite3.Row]:
    """Return all pending (or failed) send rows with job details."""
    cur = conn.execute(
        """
        SELECT
            js.id      AS send_id,
            js.job_id,
            js.topic_key,
            js.status,
            j.title,
            j.company,
            j.location,
            j.url,
            j.source,
            j.salary,
            j.job_type,
            j.work_arrangement,
            j.seniority,
            j.tags_json,
            j.is_remote
        FROM job_sends js
        JOIN jobs j ON j.id = js.job_id
        WHERE js.status IN ('pending', 'failed')
        ORDER BY js.job_id, js.topic_key
        """
    )
    return cur.fetchall()


def mark_send_ok(conn: sqlite3.Connection, send_id: int) -> None:
    conn.execute(
        "UPDATE job_sends SET status='sent', sent_at=?, error='', updated_at=? WHERE id=?",
        (_now(), _now(), send_id),
    )


def mark_send_failed(conn: sqlite3.Connection, send_id: int, error: str) -> None:
    conn.execute(
        "UPDATE job_sends SET status='failed', error=?, updated_at=? WHERE id=?",
        (error[:1000], _now(), send_id),
    )


def mark_send_skipped(conn: sqlite3.Connection, send_id: int, reason: str = "") -> None:
    """Skip a send (e.g. topic no longer configured). Won't retry."""
    conn.execute(
        "UPDATE job_sends SET status='skipped', error=?, updated_at=? WHERE id=?",
        (reason[:500], _now(), send_id),
    )


def set_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        (key, value),
    )


def get_metadata(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    cur = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,))
    row = cur.fetchone()
    return row["value"] if row else default


def already_seen(conn: sqlite3.Connection, content_hash: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM jobs WHERE content_hash = ? LIMIT 1",
        (content_hash,),
    )
    return cur.fetchone() is not None


def stats(conn: sqlite3.Connection) -> dict:
    totals = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM job_sends WHERE status='pending'"
    ).fetchone()["n"]
    failed = conn.execute(
        "SELECT COUNT(*) AS n FROM job_sends WHERE status='failed'"
    ).fetchone()["n"]
    sent = conn.execute(
        "SELECT COUNT(*) AS n FROM job_sends WHERE status='sent'"
    ).fetchone()["n"]
    return {"total_jobs": totals, "pending_sends": pending, "failed_sends": failed, "sent": sent}
