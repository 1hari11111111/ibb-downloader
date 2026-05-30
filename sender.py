"""
sender.py — Sends a downloaded file to the Telegram DB channel.
Chooses send_photo / send_video / send_document based on media type.
Deletes the local temp file after a successful send.
"""

import logging
from telegram import Bot
from telegram.error import TelegramError

from config import DB_CHANNEL_ID
from scraper import MediaItem
from downloader import delete_file

logger = logging.getLogger(__name__)

# Telegram file size limits (in bytes)
MAX_PHOTO_SIZE  = 10 * 1024 * 1024   # 10 MB  — send_photo
MAX_VIDEO_SIZE  = 50 * 1024 * 1024   # 50 MB  — send_video
MAX_DOC_SIZE    = 50 * 1024 * 1024   # 50 MB  — send_document


async def send_media(bot: Bot, item: MediaItem, local_path: str, profile: str) -> bool:
    """
    Send a single file to DB_CHANNEL_ID.
    Returns True on success, False on failure.
    Deletes the local file after a successful send.
    """
    import os
    file_size = os.path.getsize(local_path)
    caption = (
        f"👤 Profile: {profile}\n"
        f"📁 File: {item.filename}\n"
        f"🔗 Source: {item.page_url}"
    )

    try:
        with open(local_path, "rb") as f:
            if item.media_type == "photo" and file_size <= MAX_PHOTO_SIZE:
                await bot.send_photo(
                    chat_id=DB_CHANNEL_ID,
                    photo=f,
                    caption=caption,
                )
            elif item.media_type in ("video",) and file_size <= MAX_VIDEO_SIZE:
                await bot.send_video(
                    chat_id=DB_CHANNEL_ID,
                    video=f,
                    caption=caption,
                    supports_streaming=True,
                )
            elif item.media_type == "gif" and file_size <= MAX_DOC_SIZE:
                # Send GIFs as animation so Telegram auto-plays them
                await bot.send_animation(
                    chat_id=DB_CHANNEL_ID,
                    animation=f,
                    caption=caption,
                )
            else:
                # Fallback: send as document (works for any file type / large files)
                await bot.send_document(
                    chat_id=DB_CHANNEL_ID,
                    document=f,
                    caption=caption,
                    filename=item.filename,
                )

        logger.info("Sent to channel: %s", item.filename)
        delete_file(local_path)
        return True

    except TelegramError as e:
        logger.error("Telegram send failed for %s: %s", item.filename, e)
        delete_file(local_path)
        return False
    except Exception as e:
        logger.error("Unexpected error sending %s: %s", item.filename, e)
        delete_file(local_path)
        return False
