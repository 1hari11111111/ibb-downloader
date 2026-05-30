"""
main.py — IBB Downloader Telegram Bot
"""

import asyncio
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

from config import BOT_TOKEN, ADMIN_IDS
from db import init_db, is_seen, mark_seen, mark_skipped, get_stats
from scraper import scrape_profile
from downloader import download_file, ensure_temp_dir
from sender import send_media

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Global scrape state ──────────────────────────────────────────────────────
_scrape_active   = False
_scrape_cancel   = False
_scrape_progress: dict = {}

CANCEL_BUTTON = InlineKeyboardMarkup([
    [InlineKeyboardButton("🛑 Cancel", callback_data="cancel_scrape")]
])


def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else None
        if user_id not in ADMIN_IDS:
            return
        return await func(update, ctx)
    return wrapper


# ── /start ───────────────────────────────────────────────────────────────────
@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *IBB Downloader Bot*\n\n"
        "*Commands:*\n"
        "`/scrape <url>` — start scraping\n"
        "`/status` — check progress\n"
        "`/cancel` — stop current scrape\n"
        "`/stats` — lifetime statistics",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /stats ───────────────────────────────────────────────────────────────────
@admin_only
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = get_stats()
    await update.message.reply_text(
        f"📊 *Lifetime Stats*\n\n"
        f"✅ Sent: `{s.get('total_sent', 0)}`\n"
        f"⏭ Skipped (duplicates): `{s.get('total_skipped', 0)}`",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /status ──────────────────────────────────────────────────────────────────
@admin_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _scrape_active:
        await update.message.reply_text("💤 No scrape running.")
        return
    p = _scrape_progress
    await update.message.reply_text(
        f"⏳ *Scrape in progress*\n\n"
        f"👤 Profile: `{p.get('profile', '?')}`\n"
        f"📥 Progress: `{p.get('current', 0)}/{p.get('total', '?')}`\n"
        f"✅ Sent: `{p.get('sent', 0)}`\n"
        f"⏭ Skipped: `{p.get('skipped', 0)}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=CANCEL_BUTTON,
    )


# ── /cancel ──────────────────────────────────────────────────────────────────
@admin_only
async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global _scrape_cancel
    if not _scrape_active:
        await update.message.reply_text("💤 Nothing to cancel.")
        return
    _scrape_cancel = True
    await update.message.reply_text("🛑 Cancel requested. Stopping after current file...")


# ── Inline cancel button callback ────────────────────────────────────────────
async def callback_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global _scrape_cancel
    query = update.callback_query
    user_id = query.from_user.id if query.from_user else None

    await query.answer()

    if user_id not in ADMIN_IDS:
        await query.answer("⛔ Not authorised.", show_alert=True)
        return

    if not _scrape_active:
        await query.edit_message_text("💤 No scrape is running.")
        return

    _scrape_cancel = True
    await query.edit_message_text(
        "🛑 *Cancel requested!*\nStopping after current file...",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /scrape ───────────────────────────────────────────────────────────────────
@admin_only
async def cmd_scrape(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global _scrape_active, _scrape_cancel, _scrape_progress

    if _scrape_active:
        await update.message.reply_text(
            "⚠️ A scrape is already running.",
            reply_markup=CANCEL_BUTTON,
        )
        return

    args = ctx.args
    if not args:
        await update.message.reply_text(
            "Usage: `/scrape https://username.imgbb.com/`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    profile_url = args[0].strip()
    if "ibb.co" not in profile_url and "imgbb.com" not in profile_url:
        await update.message.reply_text("❌ Please provide a valid imgbb.com or ibb.co URL.")
        return

    msg = await update.message.reply_text(
        f"🔍 Starting scrape for:\n`{profile_url}`\n\nCollecting media links...",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=CANCEL_BUTTON,
    )

    _scrape_active  = True
    _scrape_cancel  = False
    _scrape_progress = {}
    bot = ctx.bot

    async def run_scrape():
        global _scrape_active, _scrape_cancel, _scrape_progress
        try:
            loop = asyncio.get_event_loop()
            items, username = await loop.run_in_executor(None, scrape_profile, profile_url)

            if not items:
                await msg.edit_text(
                    f"❌ No media found on profile: `{profile_url}`\n"
                    f"Check credentials or make sure profile exists.",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            total = len(items)
            _scrape_progress = {
                "profile": username, "total": total,
                "current": 0, "sent": 0, "skipped": 0,
            }

            await msg.edit_text(
                f"✅ Found *{total}* media items for `{username}`\n"
                f"Starting download & upload...",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=CANCEL_BUTTON,
            )

            for i, item in enumerate(items, 1):
                if _scrape_cancel:
                    await msg.edit_text(
                        f"🛑 *Cancelled at {i - 1}/{total}*\n\n"
                        f"✅ Sent: `{_scrape_progress['sent']}`\n"
                        f"⏭ Skipped: `{_scrape_progress['skipped']}`",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    return

                _scrape_progress["current"] = i

                if is_seen(item.direct_url):
                    mark_skipped()
                    _scrape_progress["skipped"] += 1
                    continue

                # Update progress with cancel button
                await msg.edit_text(
                    f"⏳ Downloading *{i}/{total}*\n"
                    f"📁 `{item.filename}`\n"
                    f"✅ Sent: {_scrape_progress['sent']} | "
                    f"⏭ Skipped: {_scrape_progress['skipped']}",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=CANCEL_BUTTON,
                )

                local_path = await loop.run_in_executor(None, download_file, item)
                if not local_path:
                    continue

                success = await send_media(bot, item, local_path, username)
                if success:
                    mark_seen(item.direct_url, username)
                    _scrape_progress["sent"] += 1

            # Done — remove cancel button
            await msg.edit_text(
                f"🎉 *Scrape complete!*\n\n"
                f"👤 Profile: `{username}`\n"
                f"📦 Total found: `{total}`\n"
                f"✅ Sent to channel: `{_scrape_progress['sent']}`\n"
                f"⏭ Skipped (duplicates): `{_scrape_progress['skipped']}`",
                parse_mode=ParseMode.MARKDOWN,
            )

        except Exception as e:
            logger.exception("Scrape failed: %s", e)
            await msg.edit_text(
                f"❌ Scrape failed:\n`{e}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        finally:
            _scrape_active  = False
            _scrape_cancel  = False

    asyncio.create_task(run_scrape())


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    init_db()
    ensure_temp_dir()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("scrape", cmd_scrape))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(CallbackQueryHandler(callback_cancel, pattern="^cancel_scrape$"))

    logger.info("Bot started. Listening for commands...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
