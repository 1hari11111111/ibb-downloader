"""
sender.py — Sends downloaded files to the Telegram DB channel as proper media types.
"""

import os
import logging
from telegram import Bot
from telegram.error import TelegramError

from config import DB_CHANNEL_ID
from scraper import MediaItem
from downloader import delete_file

logger = logging.getLogger(__name__)

# Telegram limits
MAX_PHOTO_SIZE  = 10 * 1024 * 1024   # 10 MB
MAX_VIDEO_SIZE  = 50 * 1024 * 1024   # 50 MB
MAX_DOC_SIZE    = 50 * 1024 * 1024   # 50 MB

# All supported extensions
PHOTO_EXTS    = {"jpg", "jpeg", "png", "webp", "bmp"}
GIF_EXTS      = {"gif"}
VIDEO_EXTS    = {"mp4", "mov", "avi", "mkv", "webm", "flv", "wmv", "m4v", "3gp"}
STICKER_EXTS  = {"webp"}  # already in PHOTO_EXTS but Telegram handles webp as sticker sometimes


def _get_ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


async def send_media(bot: Bot, item: MediaItem, local_path: str, profile: str) -> bool:
    """
    Send a file to DB_CHANNEL_ID using the correct Telegram method.
    - jpg/png/webp/bmp  → send_photo
    - gif               → send_animation  (auto-plays in Telegram)
    - mp4/mov/avi/etc   → send_video
    - anything else     → send_document
    Deletes the local file after sending (success or fail).
    """
    file_size = os.path.getsize(local_path)
    ext = _get_ext(item.filename)

    caption = (
        f"👤 {profile}\n"
        f"📁 {item.filename}\n"
        f"🔗 {item.page_url}"
    )

    try:
        with open(local_path, "rb") as f:

            # ── Photo ────────────────────────────────────────────────────────
            if ext in PHOTO_EXTS and file_size <= MAX_PHOTO_SIZE:
                await bot.send_photo(
                    chat_id=DB_CHANNEL_ID,
                    photo=f,
                    caption=caption,
                )

            # ── Photo too large → send as document ───────────────────────────
            elif ext in PHOTO_EXTS and file_size > MAX_PHOTO_SIZE:
                with open(local_path, "rb") as f2:
                    await bot.send_document(
                        chat_id=DB_CHANNEL_ID,
                        document=f2,
                        caption=caption,
                        filename=item.filename,
                    )

            # ── GIF → animation (auto-plays) ─────────────────────────────────
            elif ext in GIF_EXTS and file_size <= MAX_DOC_SIZE:
                await bot.send_animation(
                    chat_id=DB_CHANNEL_ID,
                    animation=f,
                    caption=caption,
                )

            # ── Video ────────────────────────────────────────────────────────
            elif ext in VIDEO_EXTS and file_size <= MAX_VIDEO_SIZE:
                await bot.send_video(
                    chat_id=DB_CHANNEL_ID,
                    video=f,
                    caption=caption,
                    supports_streaming=True,
                )

            # ── Video too large → send as document ───────────────────────────
            elif ext in VIDEO_EXTS and file_size > MAX_VIDEO_SIZE:
                with open(local_path, "rb") as f2:
                    await bot.send_document(
                        chat_id=DB_CHANNEL_ID,
                        document=f2,
                        caption=caption,
                        filename=item.filename,
                    )

            # ── Everything else → document ───────────────────────────────────
            else:
                await bot.send_document(
                    chat_id=DB_CHANNEL_ID,
                    document=f,
                    caption=caption,
                    filename=item.filename,
                )

        logger.info("Sent [%s] %s (%.1f KB)", ext.upper(), item.filename, file_size / 1024)
        delete_file(local_path)
        return True

    except TelegramError as e:
        logger.error("Telegram error sending %s: %s", item.filename, e)
        delete_file(local_path)
        return False
    except Exception as e:
        logger.error("Unexpected error sending %s: %s", item.filename, e)
        delete_file(local_path)
        return False
