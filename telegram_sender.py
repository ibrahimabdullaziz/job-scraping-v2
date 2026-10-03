"""
Telegram message formatting and per-topic sending.

Each job is formatted as a short card and sent to one or more forum topics
via message_thread_id.  Delivery state is tracked per (job, topic) in SQLite
so that a failed send can be retried without duplicating successful ones.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

import config

log = logging.getLogger(__name__)


# ─── Telegram API ─────────────────────────────────────────────────────────────

def _api_url(method: str) -> str:
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    return f"https://api.telegram.org/bot{token}/{method}"


def send_message(
    chat_id: str,
    text: str,
    thread_id: Optional[int] = None,
    parse_mode: str = "HTML",
    disable_web_page_preview: bool = True,
    max_retries: int = 3,
) -> bool:
    """
    Send a Telegram message.  Returns True on success, False on failure.
    Automatically respects Telegram rate limits (429 retry_after).
    """
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if thread_id is not None:
        payload["message_thread_id"] = thread_id

    for attempt in range(max_retries):
        try:
            resp = requests.post(_api_url("sendMessage"), json=payload, timeout=20)
            data = resp.json()
            if data.get("ok"):
                return True

            error_code = data.get("error_code")
            params = data.get("parameters", {})
            retry_after = params.get("retry_after")
            desc = data.get("description", "")

            # Telegram 429 / Rate Limit check
            if error_code == 429 or retry_after or "retry after" in desc.lower():
                wait_sec = int(retry_after) if retry_after else 15
                log.warning(
                    "Telegram 429 Rate Limit (thread_id=%s) — sleeping %ds before retry (%d/%d)",
                    thread_id,
                    wait_sec + 1,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(wait_sec + 1)
                continue

            # Permanent error (e.g. chat not found, bot kicked)
            log.error(
                "Telegram sendMessage error: %s (thread_id=%s)",
                desc or "unknown",
                thread_id,
            )
            return False

        except requests.RequestException as exc:
            log.error("Telegram request failed (thread_id=%s): %s", thread_id, exc)
            if attempt < max_retries - 1:
                time.sleep(3)
                continue
            return False

    return False


# ─── Card formatting ──────────────────────────────────────────────────────────

def _emoji_for_arrangement(arrangement: str) -> str:
    mapping = {
        "remote": "🌍",
        "hybrid": "🏠",
        "on-site": "🏢",
    }
    return mapping.get(arrangement.lower(), "📍")


def format_job_card(job_row) -> str:
    """
    Build a short Telegram job card (HTML) from a DB row returned by
    db.get_pending_sends().

    Format:
      💼 <b>Title</b>
      🏢 Company
      📍 Location · Work arrangement
      🔗 Apply → link
      📌 Source
    """
    title = job_row["title"] or "—"
    company = job_row["company"] or "—"
    location = job_row["location"] or "—"
    url = job_row["url"] or ""
    source = job_row["source"] or "—"
    arrangement = job_row["work_arrangement"] or ""
    seniority = job_row["seniority"] or ""

    arrangement_emoji = _emoji_for_arrangement(arrangement)
    loc_line = location
    if arrangement:
        loc_line = f"{location} · {arrangement}"

    lines = [
        f"💼 <b>{_esc(title)}</b>",
        f"🏢 {_esc(company)}",
        f"{arrangement_emoji} {_esc(loc_line)}",
    ]
    if seniority:
        lines.append(f"🎯 {_esc(seniority)}")
    if url:
        lines.append(f'🔗 <a href="{url}">Apply</a>')
    lines.append(f"📌 {_esc(source)}")

    return "\n".join(lines)


def _esc(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse mode."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ─── Batch delivery ───────────────────────────────────────────────────────────

def deliver_pending(conn, seed_mode: bool = False) -> dict:
    """
    Send all pending job_sends rows.

    In seed_mode, rows are marked 'sent' without actually posting to Telegram.

    Returns a summary dict with counts.
    """
    import db

    rows = db.get_pending_sends(conn)
    if not rows:
        log.info("No pending sends.")
        return {"attempted": 0, "sent": 0, "failed": 0, "skipped": 0}

    summary = {"attempted": 0, "sent": 0, "failed": 0, "skipped": 0}
    group_id = config.TELEGRAM_GROUP_ID

    if not group_id and not seed_mode:
        log.error("TELEGRAM_GROUP_ID is not set — cannot send messages")
        return summary

    for row in rows:
        topic_key = row["topic_key"]
        send_id = row["send_id"]
        summary["attempted"] += 1

        # Resolve thread ID
        thread_id = config.get_topic_thread_id(topic_key)
        if thread_id is None:
            log.warning("Topic %r has no thread ID configured — skipping send_id=%d", topic_key, send_id)
            db.mark_send_skipped(conn, send_id, "topic_not_configured")
            summary["skipped"] += 1
            continue

        if seed_mode:
            db.mark_send_ok(conn, send_id)
            summary["sent"] += 1
            continue

        card = format_job_card(row)
        ok = send_message(group_id, card, thread_id=thread_id)

        if ok:
            db.mark_send_ok(conn, send_id)
            summary["sent"] += 1
            log.info(
                "Sent job_id=%d to topic=%r (%s @ %s)",
                row["job_id"], topic_key, row["title"], row["company"],
            )
        else:
            error_msg = f"send failed at {__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}"
            db.mark_send_failed(conn, send_id, error_msg)
            summary["failed"] += 1
            log.warning(
                "FAILED job_id=%d topic=%r (%s @ %s)",
                row["job_id"], topic_key, row["title"], row["company"],
            )

        time.sleep(config.TELEGRAM_SEND_DELAY)

    conn.commit()
    log.info("Delivery summary: %s", summary)
    return summary
