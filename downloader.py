"""
downloader.py — Downloads a single MediaItem to the temp folder.
Called one file at a time (sequential) with a rate-limit delay.
"""

import os
import time
import logging
from pathlib import Path

import requests

from config import HEADERS, RATE_LIMIT_DELAY, REQUEST_TIMEOUT, MAX_RETRIES, TEMP_DIR
from scraper import MediaItem

logger = logging.getLogger(__name__)


def ensure_temp_dir() -> None:
    Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)


def download_file(item: MediaItem) -> str | None:
    """
    Download item.direct_url to TEMP_DIR.
    Returns the local file path on success, None on failure.
    Applies RATE_LIMIT_DELAY before each attempt.
    """
    ensure_temp_dir()

    # Sanitise filename
    safe_name = "".join(
        c for c in item.filename if c.isalnum() or c in "._- "
    ).strip()
    if not safe_name:
        safe_name = "file"

    local_path = os.path.join(TEMP_DIR, safe_name)

    # If a file with this name exists, append a counter
    if os.path.exists(local_path):
        base, ext = os.path.splitext(safe_name)
        counter = 1
        while os.path.exists(local_path):
            local_path = os.path.join(TEMP_DIR, f"{base}_{counter}{ext}")
            counter += 1

    time.sleep(RATE_LIMIT_DELAY)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "Downloading (attempt %d/%d): %s", attempt, MAX_RETRIES, item.direct_url
            )
            with requests.get(
                item.direct_url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                stream=True,
            ) as resp:
                resp.raise_for_status()
                with open(local_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 64):
                        if chunk:
                            f.write(chunk)

            size = os.path.getsize(local_path)
            logger.info("Downloaded %s — %.1f KB", safe_name, size / 1024)
            return local_path

        except Exception as e:
            logger.warning("Download attempt %d failed: %s", attempt, e)
            if os.path.exists(local_path):
                os.remove(local_path)
            if attempt < MAX_RETRIES:
                time.sleep(RATE_LIMIT_DELAY * attempt)

    logger.error("All download attempts failed for: %s", item.direct_url)
    return None


def delete_file(path: str) -> None:
    """Delete a temp file after it has been sent."""
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.debug("Deleted temp file: %s", path)
    except OSError as e:
        logger.warning("Could not delete temp file %s: %s", path, e)
