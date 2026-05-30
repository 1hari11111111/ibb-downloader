"""
db.py — SQLite helper for deduplication.
Stores a hash of every URL already sent so re-running the same
profile never sends duplicates.
"""

import sqlite3
import hashlib
import logging
from config import DB_FILE

logger = logging.getLogger(__name__)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_urls (
                url_hash TEXT PRIMARY KEY,
                url      TEXT NOT NULL,
                profile  TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                id            INTEGER PRIMARY KEY CHECK (id = 1),
                total_sent    INTEGER DEFAULT 0,
                total_skipped INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO stats (id, total_sent, total_skipped)
            VALUES (1, 0, 0)
        """)
        conn.commit()
    logger.info("Database initialised.")


def _hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def is_seen(url: str) -> bool:
    """Return True if this URL was already downloaded and sent."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_urls WHERE url_hash = ?", (_hash(url),)
        ).fetchone()
    return row is not None


def mark_seen(url: str, profile: str = "") -> None:
    """Record a URL as downloaded."""
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen_urls (url_hash, url, profile) VALUES (?, ?, ?)",
            (_hash(url), url, profile),
        )
        conn.execute("UPDATE stats SET total_sent = total_sent + 1 WHERE id = 1")
        conn.commit()


def mark_skipped() -> None:
    """Increment the skipped counter."""
    with _connect() as conn:
        conn.execute(
            "UPDATE stats SET total_skipped = total_skipped + 1 WHERE id = 1"
        )
        conn.commit()


def get_stats() -> dict:
    """Return total sent and skipped counts."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT total_sent, total_skipped FROM stats WHERE id = 1"
        ).fetchone()
    return {"total_sent": row[0], "total_skipped": row[1]} if row else {}
