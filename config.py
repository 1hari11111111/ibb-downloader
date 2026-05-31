# ============================================================
#  IBB Downloader Bot — Configuration
# ============================================================

# Your Telegram bot token from @BotFather
BOT_TOKEN = ""

# Telegram user IDs allowed to use the bot (admin only)
ADMIN_IDS = [8745603483]

# The channel/group ID where media will be forwarded
DB_CHANNEL_ID = -1003862938674

# imgbb login credentials (needed for private profiles)
IMGBB_EMAIL    = "nenuevaru813@gmail.com"
IMGBB_PASSWORD = "qLX$J@s3yNzdF6_"

# Delay in seconds between each file download
RATE_LIMIT_DELAY = 2.0

# Delay in seconds between fetching each image page
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
