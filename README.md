# IBB Downloader Telegram Bot

**Upload every image, GIF, video, and file from any ImgBB profile to Telegram in one clean flow.**

---

## Quick Start

1. Install dependencies  
   ```bash
   pip install -r requirements.txt
   ```

2. Set these environment variables (never commit them):  
   ```bash
   BOT_TOKEN=your_bot_token
   ADMIN_IDS=your_admin_id
   DB_CHANNEL_ID=-1001234567890   # will be overwritten by /setchannel
   IMGBB_EMAIL=optional
   IMGBB_PASSWORD=optional
   APP_DATA_DIR=/persistent/path
   ```

3. Run the bot  
   ```bash
   python main.py
   ```

---

## Commands

| Command                        | Description |
|-------------------------------|-----------|
| `/scrape https://username.imgbb.com/` | Start / resume a scrape |
| `/status`                     | Show current progress |
| `/cancel`                     | Stop gracefully |
| `/setchannel <id>`            | Save dump channel (number starts with `-100`) |
| `/stats`                      | Lifetime totals |
| `/retry_failed`               | Re-queue failed uploads |

---

## How it works

1. **Pagination** — ImgBB returns ~32 items per page. The bot continues until the full profile is discovered.
2. **Progress** — One editable status message with inline Cancel button.
3. **Cancellation** — Press the button or send `/cancel` to stop cleanly.
4. **Channel** — Uses the saved channel (`/setchannel`) or the environment value.
5. **Uploads** — Photos, GIFs, videos, and documents are sent in the chosen channel.

---

## Configurable Settings

| Setting                     | Default | Description |
|----------------------------|---------|-----------|
| `REQUEST_TIMEOUT`           | 60      | HTTP timeout |
| `MAX_RETRIES`               | 4       | Transient errors |
| `PAGE_SIZE`                | 100     | ImgBB page size |
| `PAGE_FETCH_DELAY`         | 1s      | Between pages |
| `ITEM_DELAY`               | 0.5s    | Between items |
| `MAX_PAGES`                | 100000  | Safety guard |
| `MEDIA_HOSTS`              | i.ibb.co,ibb.co,imgbb.com | Allowed hosts |

---

## Telegram Limits & Failure Handling

- Images ≤ 10 MB → photos  
- GIFs → animations  
- Videos ≤ 50 MB → videos  
- Everything else → documents  

Failed uploads stay marked `retryable` or `failed`. Use `/retry_failed` to re-queue them.

---

## Persistence Warning

SQLite and temp files need a **persistent** filesystem (Heroku workers lose them on restart). For those platforms use a managed database or replace the storage layer.

---

## Tests

```bash
pytest -q
```

Tests are offline and do not require credentials or real uploads.

---

**That's it!**  
Start with `/scrape https://yourprofile.imgbb.com/`, wait for it to finish, and all media goes to the saved channel.
