# ============================================================
#  IBB Downloader Bot — Configuration
#  Fill in all values before running the bot
# ============================================================

# Your Telegram bot token from @BotFather
BOT_TOKEN = "8538662070:AAE19w27EEfGrQSU2fs1z6KSG82fb83mhdQ"

# Telegram user IDs allowed to use the bot (admin only)
# Example: ADMIN_IDS = [123456789, 987654321]
ADMIN_IDS = [8745603483]

# The channel/group ID where media will be forwarded
# For a channel: use negative ID like -1001234567890
# To get ID: forward a message from your channel to @userinfobot
DB_CHANNEL_ID = -1003940503301

# Delay in seconds between each file download (be polite to IBB servers)
RATE_LIMIT_DELAY = 2.0

# Delay in seconds between fetching each image page on IBB
PAGE_FETCH_DELAY = 1.5

# Temporary folder to store files before sending
TEMP_DIR = "temp"

# SQLite database file for tracking already-downloaded URLs
DB_FILE = "seen_urls.db"

# Max retries for failed downloads
MAX_RETRIES = 3

# Request timeout in seconds
REQUEST_TIMEOUT = 30

# User-Agent header to mimic a real browser
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
