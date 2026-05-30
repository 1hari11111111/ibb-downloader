# IBB Downloader Telegram Bot

Downloads all public media (images, GIFs, videos) from an IBB user profile
and forwards them to a Telegram channel.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure the bot

Open `config.py` and fill in:

| Setting | Description |
|---|---|
| `BOT_TOKEN` | Your bot token from @BotFather |
| `ADMIN_IDS` | List of Telegram user IDs allowed to use the bot |
| `DB_CHANNEL_ID` | Your database channel ID (e.g. `-1001234567890`) |
| `RATE_LIMIT_DELAY` | Seconds between downloads (default: 2.0) |
| `PAGE_FETCH_DELAY` | Seconds between page fetches (default: 1.5) |

**How to get your Telegram user ID:**
Send a message to @userinfobot on Telegram.

**How to get your channel ID:**
1. Add @userinfobot to your channel as admin
2. Forward any message from your channel to @userinfobot
3. It will show the channel ID (starts with -100...)

**Make sure your bot is an admin in the DB channel** so it can post media.

### 3. Run the bot

```bash
python main.py
```

---

## Commands

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/scrape <url>` | Scrape a public IBB profile |
| `/status` | Check current scrape progress |
| `/cancel` | Cancel a running scrape |
| `/stats` | Lifetime download statistics |

**Example:**
```
/scrape https://ibb.co/user/someusername
```

---

## How it works

1. Bot receives a profile URL from admin
2. Scraper fetches all image pages from the profile (handles pagination)
3. For each image page, extracts the direct media URL
4. Downloads files one by one with a rate-limit delay
5. Sends each file to the configured Telegram channel
6. Stores URL hashes in SQLite — re-running the same profile skips duplicates

---

## Notes

- Only **public** profiles can be scraped (no login needed)
- Large profiles (100+ images) will take time — this is intentional to avoid being blocked
- Temp files are deleted immediately after being sent to Telegram
- The `seen_urls.db` file grows over time — it's your deduplication database, don't delete it
- Bot handles one scrape at a time; use /cancel to stop and start a new one

---

## File structure

```
ibb_bot/
├── main.py           # Bot entry point, command handlers
├── config.py         # All configuration (edit this!)
├── scraper.py        # IBB profile scraper
├── downloader.py     # File downloader with rate limiting
├── sender.py         # Telegram channel sender
├── db.py             # SQLite deduplication database
├── requirements.txt  # Python dependencies
├── seen_urls.db      # Created automatically on first run
└── temp/             # Created automatically, auto-cleaned
```
