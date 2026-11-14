"""Durable Telegram bot: incremental ImgBB discovery, download, and upload."""
from __future__ import annotations
import asyncio, html, logging, time
from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes
import config
import db
from downloader import DownloadError, download_file
from scraper import MediaItem, ScrapeError, create_session, fetch_page, username_from_url
from sender import SendError, send_media

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s - %(message)s", level=logging.INFO)
# httpx logs Telegram bot tokens inside request URLs at INFO level.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Application").setLevel(logging.INFO)
logger = logging.getLogger(__name__)
_worker: asyncio.Task | None = None

def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id not in config.ADMIN_IDS: return
        return await func(update, ctx)
    return wrapper

def _text(value) -> str:
    return html.escape(str(value or ""), quote=False)

async def _safe_edit(message, text: str) -> None:
    try: await message.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception as exc: logger.debug("Progress edit skipped: %s", exc)

async def _run_worker(job_id: str, bot: Bot, message) -> None:
    session = create_session(); last_update = 0.0
    try:
        while True:
            job = db.get_job(job_id)
            if not job or db.cancelled(job_id):
                db.job_status(job_id, "cancelled"); return
            if db.pending(job_id) == 0:
                page, seek = job["page"], job["seek"]
                if page > config.MAX_PAGES: break
                items, next_seek, complete = await asyncio.to_thread(fetch_page, session, job["username"], page, seek)
                db.add_items(job_id, [x.__dict__ for x in items])
                if complete:
                    db.job_status(job_id, "complete"); return
                # Some ImgBB responses expose no cursor and paginate solely by
                # page number.  Continue in that mode until the endpoint
                # returns an empty batch; cursor-based responses still use the
                # returned seek value.
                if next_seek and next_seek == seek:
                    raise ScrapeError("Pagination made no progress")
                db.save_cursor(job_id, page + 1, next_seek)
                await asyncio.sleep(config.PAGE_FETCH_DELAY); continue
            item = db.claim(job_id)
            if not item: await asyncio.sleep(1); continue
            media = MediaItem(item["page_url"], item["direct_url"], item["filename"], item["media_type"])
            try:
                path = await asyncio.to_thread(download_file, media, item["id"], lambda: db.cancelled(job_id))
                db.downloaded(item["id"], path)
                message_id = await send_media(bot, media, path)
                db.sent(item["id"], message_id)
            except DownloadError as exc:
                db.failure(item["id"], exc, exc.retryable)
            except SendError as exc:
                db.failure(item["id"], exc, exc.retryable)
            except Exception as exc:
                logger.exception("Item %s failed", item["id"]); db.failure(item["id"], exc, True)
            if time.monotonic() - last_update >= config.PROGRESS_INTERVAL:
                last_update = time.monotonic(); job = db.get_job(job_id)
                await _safe_edit(message, f"<b>Scrape in progress</b>\nProfile: <code>{_text(job['username'])}</code>\nDiscovered: {job['discovered']}\nSent: <b>{job['sent']}</b> | Skipped: {job['skipped']} | Failed: {job['failed']}\nQueue: {db.pending(job_id)}\nCancel with /cancel")
            await asyncio.sleep(config.RATE_LIMIT_DELAY)
    except asyncio.CancelledError:
        db.job_status(job_id, "cancelled"); raise
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        # Persist a string because SQLite cannot bind arbitrary exception objects.
        db.job_status(job_id, "failed", str(exc))
        await _safe_edit(message, f"❌ Scrape failed: {_text(str(exc)[:500])}")

@admin_only
async def cmd_start(update, ctx):
    await update.message.reply_text("IBB Downloader\n/scrape <url> — start or resume\n/status — durable progress\n/cancel — stop gracefully\n/stats — lifetime totals\n/retry_failed — requeue terminal failures")
@admin_only
async def cmd_stats(update, ctx):
    s = db.stats(); await update.message.reply_text(f"Sent: {s.get('total_sent',0)} | Skipped: {s.get('total_skipped',0)} | Failed: {s.get('total_failed',0)}")
@admin_only
async def cmd_status(update, ctx):
    job = db.active_job()
    if not job: await update.message.reply_text("No active scrape."); return
    await update.message.reply_text(f"Profile: {job['username']}\nStatus: {job['status']}\nDiscovered: {job['discovered']}\nSent: {job['sent']} | Skipped: {job['skipped']} | Failed: {job['failed']}\nQueue: {db.pending(job['id'])}")
@admin_only
async def cmd_cancel(update, ctx):
    job = db.active_job()
    if not job: await update.message.reply_text("Nothing to cancel."); return
    db.cancel(job["id"]); await update.message.reply_text("Cancellation requested; stopping safely.")
@admin_only
async def cmd_scrape(update, ctx):
    global _worker
    if _worker and not _worker.done(): await update.message.reply_text("A scrape is already running. Use /status."); return
    existing = db.active_job()
    if existing:
        job_id = existing["id"]
        await update.message.reply_text(f"Resuming {existing['username']} ({db.pending(job_id)} items pending).")
        message = await update.message.reply_text("Resuming...")
    else:
        if not ctx.args: await update.message.reply_text("Usage: /scrape https://username.imgbb.com/"); return
        try: username = username_from_url(ctx.args[0])
        except ValueError as exc: await update.message.reply_text(str(exc)); return
        job_id = db.create_job(ctx.args[0].strip(), username)
        message = await update.message.reply_text(f"Started {username}. Checking existing progress...")
    _worker = asyncio.create_task(_run_worker(job_id, ctx.bot, message))
@admin_only
async def cmd_retry_failed(update, ctx):
    with db.connect() as conn:
        count = conn.execute("UPDATE items SET status='queued',next_attempt_at=0 WHERE status='failed'").rowcount
    await update.message.reply_text(f"Requeued {count} failed items. Start or resume the job with /scrape.")
def main():
    settings = config.Settings.load(require_runtime=True); settings.ensure_paths()
    db.init_db()
    app = Application.builder().token(settings.bot_token).connect_timeout(30).read_timeout(settings.telegram_timeout).build()
    for name, handler in (("start",cmd_start),("scrape",cmd_scrape),("status",cmd_status),("cancel",cmd_cancel),("stats",cmd_stats),("retry_failed",cmd_retry_failed)):
        app.add_handler(CommandHandler(name, handler))
    app.run_polling(allowed_updates=Update.ALL_TYPES)
if __name__ == "__main__": main()
