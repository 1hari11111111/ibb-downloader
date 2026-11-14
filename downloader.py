"""Bounded, atomic streaming downloader with safe host validation."""
from __future__ import annotations
import logging, os, random, re, time
from pathlib import Path
import requests
from config import HEADERS, MAX_DOWNLOAD_BYTES, MAX_RETRIES, REQUEST_TIMEOUT, TEMP_DIR
from scraper import MediaItem, normalize_media_url

logger = logging.getLogger(__name__)
SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")

class DownloadError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message); self.retryable = retryable

def ensure_temp_dir() -> None:
    Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)

def _retryable_status(status: int) -> bool:
    return status in (408, 425, 429) or status >= 500

def download_file(item: MediaItem, item_id: int | None = None, cancelled=None) -> str:
    """Download to a unique .part file, enforce a hard size cap, then rename atomically."""
    url = normalize_media_url(item.direct_url)
    if not url: raise DownloadError("Media URL is not an approved HTTPS host")
    ensure_temp_dir()
    safe_name = SAFE_NAME.sub("_", item.filename).strip(" .") or "media"
    target = Path(TEMP_DIR) / f"{item_id or 'x'}_{safe_name}"
    part = target.with_suffix(target.suffix + ".part")
    try:
        for attempt in range(MAX_RETRIES + 1):
            if cancelled and cancelled(): raise DownloadError("Cancelled", False)
            try:
                with requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True, allow_redirects=True) as response:
                    if _retryable_status(response.status_code): raise DownloadError(f"HTTP {response.status_code}", True)
                    if response.status_code >= 400: raise DownloadError(f"HTTP {response.status_code}", False)
                    final_url = normalize_media_url(response.url)
                    if not final_url or final_url != response.url and not final_url.startswith(("https://i.ibb.co/", "https://ibb.co/", "https://imgbb.com/")):
                        raise DownloadError("Redirected to unapproved host")
                    declared = int(response.headers.get("Content-Length") or 0)
                    if declared > MAX_DOWNLOAD_BYTES: raise DownloadError("File exceeds MAX_DOWNLOAD_BYTES")
                    total = 0
                    with part.open("wb") as handle:
                        for chunk in response.iter_content(64 * 1024):
                            if cancelled and cancelled(): raise DownloadError("Cancelled", False)
                            if not chunk: continue
                            total += len(chunk)
                            if total > MAX_DOWNLOAD_BYTES: raise DownloadError("File exceeds MAX_DOWNLOAD_BYTES")
                            handle.write(chunk)
                    if total == 0: raise DownloadError("Empty response", True)
                    os.replace(part, target)
                    return str(target)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if part.exists(): part.unlink()
                if attempt == MAX_RETRIES: raise DownloadError(str(exc), True) from exc
                time.sleep(min(2 ** attempt, 60) * random.uniform(1, 1.3))
            except DownloadError as exc:
                if part.exists(): part.unlink()
                if not exc.retryable or attempt == MAX_RETRIES: raise
                time.sleep(min(2 ** attempt, 60) * random.uniform(1, 1.3))
    finally:
        if part.exists(): part.unlink(missing_ok=True)
    raise DownloadError("Download failed")

def delete_file(path: str) -> None:
    try:
        if path and os.path.exists(path): os.remove(path)
    except OSError as exc:
        logger.warning("Could not delete %s: %s", path, exc)
