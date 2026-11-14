# IBB Downloader Telegram Bot

Incrementally downloads every image, GIF, video, and other media item from an
ImgBB profile and uploads it to a Telegram channel. Jobs are stored in SQLite,
so interrupted runs resume instead of restarting and failed uploads remain
retryable.

## Requirements

- Python 3.11+
- A Telegram bot that is an administrator in the destination channel
- ImgBB email and password for private profiles or profiles whose API requires authentication

## Setup

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Set environment variables. Do not put credentials in `config.py` or commit
   them.

   ```bash
   BOT_TOKEN=...
   ADMIN_IDS=123456789
   DB_CHANNEL_ID=-1001234567890
   IMGBB_EMAIL=optional@example.com
   IMGBB_PASSWORD=optional-secret
   APP_DATA_DIR=/persistent/path
   ```

   `APP_DATA_DIR` must be a persistent directory. `DB_FILE` and `TEMP_DIR` can
   override the database and temporary-file locations.

3. Run:

   ```bash
   python main.py
   ```

## Commands

| Command | Purpose |
|---|---|
| `/scrape <profile-url>` | Start a new job, or resume the active interrupted job |
| `/status` | Show durable progress from SQLite |
| `/cancel` | Request a graceful stop |
| `/stats` | Lifetime sent, skipped, and failed totals |
| `/retry_failed` | Requeue terminal failures, then resume with `/scrape` |

Accepted profile URL forms:

- `https://USERNAME.imgbb.com/`
- `https://ibb.co/user/USERNAME`

Only HTTPS media links on the configured `MEDIA_HOSTS` are downloaded. The
default list is `i.ibb.co,ibb.co,imgbb.com`.

## How it works

1. The scraper fetches one page at a time and stores discovered items in SQLite. ImgBB may return a fixed batch of about 32 items even when `PAGE_SIZE` is higher, so pagination continues by page number until an empty page is returned.
2. When ImgBB supplies a seek cursor, that cursor is persisted along with the next page number; otherwise only the page number is advanced. Progress is persisted only after a page is processed.
3. The worker claims one item at a time and downloads it to a unique `.part` file.
4. Downloads enforce `MAX_DOWNLOAD_BYTES`, validate the final host, and rename
   atomically only after success.
5. Telegram uploads use resumable file preservation: a failed upload keeps the
   local file and is marked `retryable` or `failed`.
6. On restart, stale `downloading` or `sending` items return to `queued`.

## Configurable settings

| Variable | Default | Description |
|---|---:|---|
| `REQUEST_TIMEOUT` | 60 | Download/HTTP timeout, seconds |
| `TELEGRAM_TIMEOUT` | 300 | Telegram upload read timeout |
| `MAX_RETRIES` | 4 | Retries for transient errors |
| `MAX_DOWNLOAD_BYTES` | 2 GiB | Hard download limit |
| `PAGE_SIZE` | 100 | ImgBB page request size |
| `PAGE_FETCH_DELAY` | 1 | Delay between page requests |
| `ITEM_DELAY` | 0.5 | Delay between items |
| `PROGRESS_INTERVAL` | 15 | Minimum seconds between progress edits |
| `LEASE_SECONDS` | 900 | Stale-item recovery threshold |
| `MAX_PAGES` | 100000 | Safety page guard |
| `MEDIA_HOSTS` | `i.ibb.co,ibb.co,imgbb.com` | Approved download hosts |

## Telegram limits and failure behavior

Telegram rejects some files by media type or size. This bot sends:

- Images up to 10 MB as photos
- GIFs as animations
- Videos up to 50 MB as videos
- Everything else, including larger videos and oversized images, as documents
- Documents above 2 GB are marked as permanent failures

These are conservative defaults. Actual Bot API limits depend on your Telegram
deployment. A file that cannot be uploaded is recorded with its error; it is not
silently deleted. Use `/retry_failed` after resolving a persistent issue.

## Expected runtime

The job is intentionally sequential to avoid throttling and duplicate uploads.
For approximately 5,697 items, expect several hours to potentially half a day,
depending on file sizes, Telegram response times, and retries. You can inspect
progress with `/status` and stop safely with `/cancel`.

## Persistence warning

SQLite and temporary files require a persistent filesystem. Heroku workers and
similar ephemeral containers lose local files on restart. For those platforms,
use a persistent host or replace the SQLite storage layer with a managed
database and durable object storage. Back up `seen_urls.db` before changing
deployment infrastructure.

## Tests

```bash
pytest -q
```

Tests are offline and use mocked HTTP and Telegram behavior; they do not upload
your media or require credentials.
