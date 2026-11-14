"""Durable Telegram bot: incremental ImgBB discovery, download, and upload."""
from __future__ import annotations

import asyncio
import html
import logging
import time

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import config
import db
from downloader import DownloadError, download_file
from scraper import MediaItem, ScrapeError, create_session, fetch_page, username_from_url
from sender import SendError, send_media

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Application").setLevel(logging.INFO)
logger = logging.getLogger(__name__)
_worker: asyncio.Task | None = None


def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id not in config.ADMIN_IDS:
            return
        return await func(update, ctx)
    return wrapper


def _text(value) -> str:
    return html.escape(str(value or ""), quote=False)


def _cancel_markup(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Cancel scrape", callback_data=f"cancel:{job_id}")]
    ])


def _progress_text(job) -> str:
    return (
        f"<b>Scrape in progress</b>\n"
        f"Profile: <code>{_text(job['username'])}</code>\n"
        f"Discovered: {job['discovered']}\n"
        f"Sent: <b>{job['sent']}</b> | Skipped: {job['skipped']} | Failed: {job['failed']}\n"
        f"Queue: {db.pending(job['id'])}"
    )


async def _safe_edit(message, text: str, job_id: str | None = None) -> None:
    try:
        await message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_cancel_markup(job_id) if job_id else None,
        )
    except TelegramError as exc:
        logger.debug("Progress edit skipped: %s", exc)


async def _run_worker(job_id: str, bot: Bot, message) -> None:
    session = create_session()
    last_update = 0.0
    try:
        while True:
            job = db.get_job(job_id)
            if not job or db.cancelled(job_id):
                if job:
                    db.job_status(job_id, "cancelled")
                    await _safe_edit(
                        message,
                        f"<b>Scrape cancelled</b>\nProfile: <code>{_text(job['username'])}</code>\n"
                        f"Discovered: {job['discovered']}\nSent: <b>{job['sent']}</b>",
                    )
                return

            if db.pending(job_id) == 0:
                page, seek = job["page"], job["seek"]
                if page > config.MAX_PAGES:
                    db.job_status(job_id, "complete")
                    return

                items, next_seek, complete = await asyncio.to_thread(
                    fetch_page, session, job["username"], page, seek
                )
                db.add_items(job_id, [item.__dict__ for item in items])

                if complete:
                    db.job_status(job_id, "complete")
                    final = db.get_job(job_id)
                    await _safe_edit(
                        message,
                        f"<b>Scrape complete</b>\nProfile: <code>{_text(final['username'])}</code>\n"
                        f"Discovered: {final['discovered']}\nSent: <b>{final['sent']}</b>",
                    )
                    return

                if next_seek and next_seek == seek:
                    raise ScrapeError("Pagination made no progress")
                db.save_cursor(job_id, page + 1, next_seek)
                await asyncio.sleep(config.PAGE_FETCH_DELAY)
                continue

            item = db.claim(job_id)
            if not item:
                await asyncio.sleep(1)
                continue

            media = MediaItem(
                item["page_url"], item["direct_url"], item["filename"], item["media_type"]
            )
            try:
                path = await asyncio.to_thread(
                    download_file, media, item["id"], lambda: db.cancelled(job_id)
                )
                db.downloaded(item["id"], path)
                message_id = await send_media(bot, media, path)
                db.sent(item["id"], message_id)
            except DownloadError as exc:
                db.failure(item["id"], exc, exc.retryable)
            except SendError as exc:
                db.failure(item["id"], exc, exc.retryable)
            except Exception as exc:
                logger.exception("Item %s failed", item["id"])
                db.failure(item["id"], exc, True)

            if time.monotonic() - last_update >= config.PROGRESS_INTERVAL:
                last_update = time.monotonic()
                job = db.get_job(job_id)
                if job:
                    await _safe_edit(message, _progress_text(job), job_id)
            await asyncio.sleep(config.RATE_LIMIT_DELAY)
    except asyncio.CancelledError:
        db.job_status(job_id, "cancelled")
        raise
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        db.job_status(job_id, "failed", str(exc))
        await _safe_edit(message, f"<b>Scrape failed</b>\n{_text(str(exc)[:500])}")


@admin_only
async def cmd_start(update, ctx):
    await update.message.reply_text(
        "IBB Downloader\n"
        "/scrape <url> — start or resume\n"
        "/status — show progress\n"
        "/cancel — stop gracefully\n"
        "/setchannel <channel_id> — save dump channel\n"
        "Reply to a forwarded channel post with /setchannel to select it\n"
        "/stats — lifetime totals\n"
        "/retry_failed — requeue terminal failures"
    )


@admin_only
async def cmd_stats(update, ctx):
    stats = db.stats()
    await update.message.reply_text(
        f"Sent: {stats.get('total_sent', 0)} | "
        f"Skipped: {stats.get('total_skipped', 0)} | "
        f"Failed: {stats.get('total_failed', 0)}"
    )


@admin_only
async def cmd_status(update, ctx):
    job = db.active_job()
    if not job:
        await update.message.reply_text("No active scrape.")
        return
    await update.message.reply_text(
        _progress_text(job),
        parse_mode=ParseMode.HTML,
        reply_markup=_cancel_markup(job["id"]),
    )


@admin_only
async def cmd_cancel(update, ctx):
    job = db.active_job()
    if not job:
        await update.message.reply_text("Nothing to cancel.")
        return
    db.cancel(job["id"])
    await update.message.reply_text("Cancellation requested; stopping safely.")


@admin_only
async def cmd_setchannel(update, ctx):
    """Save a channel ID directly or from a replied-to forwarded channel post."""
    channel_id = None
    if ctx.args:
        try:
            channel_id = int(ctx.args[0])
        except ValueError:
            await update.message.reply_text(
                "Usage: /setchannel -1001234567890, or reply to a forwarded channel post with /setchannel"
            )
            return
    else:
        reply = update.message.reply_to_message
        origin = getattr(reply, "forward_origin", None) if reply else None
        forwarded_chat = getattr(reply, "forward_from_chat", None) if reply else None
        chat = getattr(origin, "chat", None) or forwarded_chat
        channel_id = getattr(chat, "id", None)

    if channel_id is None or channel_id >= 0:
        await update.message.reply_text(
            "Could not identify a channel. Use its numeric ID (usually starts with -100) "
            "or reply to a forwarded channel post."
        )
        return

    try:
        chat = await ctx.bot.get_chat(channel_id)
    except TelegramError as exc:
        await update.message.reply_text(f"I cannot access that channel: {_text(exc)}")
        return

    if getattr(chat, "type", None) != "channel":
        await update.message.reply_text("That ID is not a Telegram channel.")
        return

    db.set_setting("dump_channel_id", str(channel_id))
    await update.message.reply_text(
        f"Dump channel saved: <code>{channel_id}</code> ({_text(getattr(chat, 'title', '') or 'channel')})",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def on_cancel_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer("Cancellation requested")
    job_id = (query.data or "").removeprefix("cancel:")
    job = db.get_job(job_id)
    if not job or job["status"] not in ("queued", "running", "cancelling"):
        await query.edit_message_text("No active scrape to cancel.")
        return
    db.cancel(job_id)
    await query.edit_message_text(
        f"<b>Cancellation requested</b>\nProfile: <code>{_text(job['username'])}</code>\n"
        f"Discovered: {job['discovered']}\nSent: <b>{job['sent']}</b>\nStopping safely...",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cmd_scrape(update, ctx):
    global _worker

    dump_channel = db.get_setting("dump_channel_id")
    if not dump_channel:
        await update.message.reply_text(
            "Scrape cancelled: no dump channel is configured.\n"
            "Set one first with /setchannel -1001234567890, or reply to a forwarded channel post with /setchannel."
        )
        return

    if _worker and not _worker.done():
        await update.message.reply_text("A scrape is already running. Use /status.")
        return

    existing = db.active_job()
    if existing:
        job_id = existing["id"]
        message = await update.message.reply_text(
            _progress_text(existing),
            parse_mode=ParseMode.HTML,
            reply_markup=_cancel_markup(job_id),
        )
    else:
        if not ctx.args:
            await update.message.reply_text("Usage: /scrape https://username.imgbb.com/")
            return
        try:
            username = username_from_url(ctx.args[0])
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return
        job_id = db.create_job(ctx.args[0].strip(), username)
        message = await update.message.reply_text(
            "Starting scrape...",
            reply_markup=_cancel_markup(job_id),
        )

    _worker = asyncio.create_task(_run_worker(job_id, ctx.bot, message))


@admin_only
async def cmd_retry_failed(update, ctx):
    with db.connect() as conn:
        count = conn.execute(
            "UPDATE items SET status='queued', next_attempt_at=0 WHERE status='failed'"
        ).rowcount
    await update.message.reply_text(
        f"Requeued {count} failed items. Start or resume the job with /scrape."
    )


def main():
    settings = config.Settings.load(require_runtime=True)
    settings.ensure_paths()
    db.init_db()
    app = (
        Application.builder()
        .token(settings.bot_token)
        .connect_timeout(30)
        .read_timeout(settings.telegram_timeout)
        .build()
    )
    handlers = (
        ("start", cmd_start),
        ("scrape", cmd_scrape),
        ("status", cmd_status),
        ("cancel", cmd_cancel),
        ("setchannel", cmd_setchannel),
        ("stats", cmd_stats),
        ("retry_failed", cmd_retry_failed),
    )
    for name, handler in handlers:
        app.add_handler(CommandHandler(name, handler))
    app.add_handler(CallbackQueryHandler(on_cancel_button, pattern=r"^cancel:"))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
