"""
Main worker loop.

Runs continuously on Railway:
  1. Fetch all enabled sources.
  2. Filter and classify each job.
  3. Upsert new jobs into SQLite (deduplication).
  4. Queue delivery rows for each new job × target topic.
  5. Send all pending rows to Telegram.
  6. Sleep for FETCH_INTERVAL_SECONDS and repeat.

Operational features:
  - Structured JSON logs for Railway log viewer.
  - Health-check file written after every successful cycle.
  - SEED_MODE=1 stores jobs without posting (for launch prep).
  - Graceful SIGTERM/SIGINT shutdown.
  - Source errors are logged and counted, not silently discarded.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import config
import db
import filter as job_filter
import telegram_sender
from sources import ALL_FETCHERS

# ─── Logging setup ────────────────────────────────────────────────────────────

class _JSONFormatter(logging.Formatter):
    """Emit one JSON object per log line for Railway structured logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JSONFormatter())
    root.handlers.clear()
    root.addHandler(handler)


log = logging.getLogger(__name__)

# ─── Health-check file ────────────────────────────────────────────────────────
HEALTH_FILE = Path(os.environ.get("HEALTH_FILE", "/tmp/worker_healthy"))


def _write_health(cycle: int, stats: dict) -> None:
    try:
        payload = {
            "ok": True,
            "cycle": cycle,
            "ts": datetime.now(timezone.utc).isoformat(),
            **stats,
        }
        HEALTH_FILE.write_text(json.dumps(payload))
    except Exception as exc:
        log.warning("Could not write health file: %s", exc)


# ─── Shutdown flag ────────────────────────────────────────────────────────────
_STOP = False


def _handle_signal(signum, frame):
    global _STOP
    log.info("Received signal %d — shutting down after this cycle", signum)
    _STOP = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ─── Single fetch-filter-store cycle ─────────────────────────────────────────

def run_cycle(db_path: str, seed_mode: bool) -> dict:
    """Execute one full fetch → filter → store → send cycle."""
    cycle_stats: dict = {
        "source_errors": 0,
        "fetched": 0,
        "passed_filter": 0,
        "new_jobs": 0,
        "sent": 0,
        "send_failed": 0,
    }

    all_jobs = []

    for source_name, fetcher in ALL_FETCHERS:
        try:
            log.info("Fetching source: %s", source_name)
            jobs = fetcher()
            log.info("Source %s returned %d jobs", source_name, len(jobs))
            all_jobs.extend(jobs)
            cycle_stats["fetched"] += len(jobs)
        except Exception as exc:
            log.error("Source %s FAILED: %s", source_name, exc, exc_info=True)
            cycle_stats["source_errors"] += 1

    # Filter
    included = [j for j in all_jobs if job_filter.should_include(j)]
    cycle_stats["passed_filter"] = len(included)
    log.info("Filter: %d / %d jobs passed", len(included), len(all_jobs))

    with db.connect(db_path) as conn:
        # Store and queue
        for job in included:
            topics = job_filter.route_job(job)
            if not topics:
                log.debug("No topics for: %s @ %s", job.title, job.company)
                continue

            # Only queue topics that are currently configured
            enabled = config.enabled_topics()
            active_topics = [t for t in topics if t in enabled]
            if not active_topics:
                log.debug("All topics disabled for: %s", job.title)
                continue

            job_id, is_new = db.upsert_job(conn, job)
            if is_new:
                db.init_sends(conn, job_id, active_topics)
                cycle_stats["new_jobs"] += 1
                log.info(
                    "NEW job_id=%d topics=%s: %s @ %s (%s)",
                    job_id, active_topics, job.title, job.company, job.location,
                )

        # Deliver
        delivery = telegram_sender.deliver_pending(conn, seed_mode=seed_mode)
        cycle_stats["sent"] = delivery.get("sent", 0)
        cycle_stats["send_failed"] = delivery.get("failed", 0)

        db_stats = db.stats(conn)
        cycle_stats.update(db_stats)

    return cycle_stats


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    _setup_logging()
    log.info("Worker starting — seed_mode=%s interval=%ds db=%s",
             config.SEED_MODE, config.FETCH_INTERVAL_SECONDS, config.DB_PATH)

    if not config.TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set — bot will not send messages")
    if not config.TELEGRAM_GROUP_ID:
        log.error("TELEGRAM_GROUP_ID not set — bot will not send messages")

    cycle = 0
    while not _STOP:
        cycle += 1
        log.info("=== Cycle %d starting ===", cycle)
        start = time.monotonic()

        try:
            stats = run_cycle(config.DB_PATH, seed_mode=config.SEED_MODE)
            elapsed = time.monotonic() - start
            log.info("=== Cycle %d done in %.1fs: %s ===", cycle, elapsed, stats)
            _write_health(cycle, stats)
        except Exception as exc:
            log.error("Cycle %d CRASHED: %s", cycle, exc, exc_info=True)

        if _STOP:
            break

        log.info("Sleeping %ds until next cycle", config.FETCH_INTERVAL_SECONDS)
        # Interruptible sleep — wake up every second to check _STOP
        for _ in range(config.FETCH_INTERVAL_SECONDS):
            if _STOP:
                break
            time.sleep(1)

    log.info("Worker stopped.")


if __name__ == "__main__":
    main()
