"""Telegram upload logic with size preflight, retries, and preservation on failure."""
from __future__ import annotations
import asyncio, logging, os
from telegram import Bot
from telegram.error import RetryAfter, TimedOut, NetworkError, TelegramError
from config import DB_CHANNEL_ID, MAX_RETRIES
from downloader import delete_file
from scraper import MediaItem

logger = logging.getLogger(__name__)
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_DOCUMENT_BYTES = 2000 * 1024 * 1024
PHOTO_EXT = {"jpg", "jpeg", "png", "webp", "bmp"}
GIF_EXT = {"gif"}
VIDEO_EXT = {"mp4", "mov", "avi", "mkv", "webm", "flv", "wmv", "m4v", "3gp"}

class SendError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message); self.retryable = retryable

def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

async def send_media(bot: Bot, item: MediaItem, local_path: str, profile: str = "") -> int | None:
    """Upload a file. Returns Telegram message ID; raises SendError on failure.

    The local file is deleted only after a successful upload. Retryable failures
    preserve it so the durable job can retry without downloading again.
    """
    size = os.path.getsize(local_path); ext = _ext(item.filename)
    if size > MAX_DOCUMENT_BYTES: raise SendError("File exceeds Telegram document limit")
    for attempt in range(MAX_RETRIES + 1):
        try:
            with open(local_path, "rb") as handle:
                if ext in PHOTO_EXT and size <= MAX_PHOTO_BYTES:
                    message = await bot.send_photo(chat_id=DB_CHANNEL_ID, photo=handle)
                elif ext in GIF_EXT:
                    message = await bot.send_animation(chat_id=DB_CHANNEL_ID, animation=handle)
                elif ext in VIDEO_EXT and size <= MAX_VIDEO_BYTES:
                    message = await bot.send_video(chat_id=DB_CHANNEL_ID, video=handle, supports_streaming=True)
                else:
                    message = await bot.send_document(chat_id=DB_CHANNEL_ID, document=handle, filename=item.filename)
            delete_file(local_path)
            return getattr(message, "message_id", None)
        except RetryAfter as exc:
            if attempt == MAX_RETRIES: raise SendError("Telegram rate limit", True) from exc
            await asyncio.sleep(float(exc.retry_after))
        except (TimedOut, NetworkError) as exc:
            if attempt == MAX_RETRIES: raise SendError(str(exc), True) from exc
            await asyncio.sleep(min(2 ** attempt, 60))
        except TelegramError as exc:
            retryable = any(token in str(exc).lower() for token in ("too many", "timeout", "server", "bad gateway", "try again"))
            if retryable and attempt < MAX_RETRIES:
                await asyncio.sleep(min(2 ** attempt, 60)); continue
            raise SendError(str(exc), retryable) from exc
    raise SendError("Upload failed")
