"""
sender.py — Sends downloaded files to Telegram DB channel with no captions.
"""

import os
import logging
from telegram import Bot
from telegram.error import TelegramError

from config import DB_CHANNEL_ID
from scraper import MediaItem
from downloader import delete_file

logger = logging.getLogger(__name__)

MAX_PHOTO_SIZE = 10 * 1024 * 1024   # 10 MB
MAX_VIDEO_SIZE = 50 * 1024 * 1024   # 50 MB

PHOTO_EXTS = {"jpg", "jpeg", "png", "webp", "bmp"}
GIF_EXTS   = {"gif"}
VIDEO_EXTS = {"mp4", "mov", "avi", "mkv", "webm", "flv", "wmv", "m4v", "3gp"}


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


async def send_media(bot: Bot, item: MediaItem, local_path: str, profile: str) -> bool:
    file_size = os.path.getsize(local_path)
    ext = _ext(item.filename)

    try:
        with open(local_path, "rb") as f:
            if ext in PHOTO_EXTS and file_size <= MAX_PHOTO_SIZE:
                await bot.send_photo(chat_id=DB_CHANNEL_ID, photo=f)

            elif ext in PHOTO_EXTS and file_size > MAX_PHOTO_SIZE:
                # Compress and resend as photo
                from PIL import Image
                import io
                img = Image.open(local_path)
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=85)
                buf.seek(0)
                await bot.send_photo(chat_id=DB_CHANNEL_ID, photo=buf)

            elif ext in GIF_EXTS:
                await bot.send_animation(chat_id=DB_CHANNEL_ID, animation=f)

            elif ext in VIDEO_EXTS and file_size <= MAX_VIDEO_SIZE:
                await bot.send_video(chat_id=DB_CHANNEL_ID, video=f, supports_streaming=True)

            elif ext in VIDEO_EXTS and file_size > MAX_VIDEO_SIZE:
                await bot.send_document(chat_id=DB_CHANNEL_ID, document=f, filename=item.filename)

            else:
                await bot.send_document(chat_id=DB_CHANNEL_ID, document=f, filename=item.filename)

        logger.info("Sent [%s] %s (%.1f KB)", ext.upper(), item.filename, file_size / 1024)
        delete_file(local_path)
        return True

    except TelegramError as e:
        logger.error("Telegram error sending %s: %s", item.filename, e)
        delete_file(local_path)
        return False
    except Exception as e:
        logger.error("Error sending %s: %s", item.filename, e)
        delete_file(local_path)
        return False
