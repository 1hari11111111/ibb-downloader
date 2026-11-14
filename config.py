"""Environment-backed configuration. Never store real credentials in this file.

Set these placeholders as environment variables before starting the bot:

    BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
    ADMIN_IDS=YOUR_TELEGRAM_USER_ID
    DB_CHANNEL_ID=-1001234567890
    IMGBB_EMAIL=YOUR_IMGBB_EMAIL
    IMGBB_PASSWORD=YOUR_IMGBB_PASSWORD

Use the real values only in the host or deployment secret store. Do not commit
or paste them into this file.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

def _int(name: str, default: int, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum: raise ValueError(f"{name} must be >= {minimum}")
    return value

def _float(name: str, default: float, minimum: float = 0.0) -> float:
    value = float(os.getenv(name, str(default)))
    if value < minimum: raise ValueError(f"{name} must be >= {minimum}")
    return value

def _ids(name: str) -> tuple[int, ...]:
    return tuple(int(x.strip()) for x in os.getenv(name, "").split(",") if x.strip())

@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: tuple[int, ...]
    channel_id: int
    imgbb_email: str
    imgbb_password: str
    db_file: Path
    temp_dir: Path
    timeout: float
    telegram_timeout: float
    retries: int
    max_download_bytes: int
    page_size: int
    page_delay: float
    item_delay: float
    progress_interval: float
    lease_seconds: int
    max_pages: int
    media_hosts: tuple[str, ...]

    @classmethod
    def load(cls, require_runtime: bool = False) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        admins = _ids("ADMIN_IDS")
        channel = os.getenv("DB_CHANNEL_ID", "").strip()
        if require_runtime:
            missing = [k for k, v in (("BOT_TOKEN", token), ("ADMIN_IDS", admins), ("DB_CHANNEL_ID", channel)) if not v]
            if missing: raise ValueError("Missing required environment variables: " + ", ".join(missing))
        root = Path(os.getenv("APP_DATA_DIR", ".")).expanduser().resolve()
        return cls(token, admins, int(channel or 0), os.getenv("IMGBB_EMAIL", ""), os.getenv("IMGBB_PASSWORD", ""),
            Path(os.getenv("DB_FILE", str(root / "seen_urls.db"))).expanduser().resolve(),
            Path(os.getenv("TEMP_DIR", str(root / "temp"))).expanduser().resolve(),
            _float("REQUEST_TIMEOUT", 60, 1), _float("TELEGRAM_TIMEOUT", 300, 1), _int("MAX_RETRIES", 4),
            _int("MAX_DOWNLOAD_BYTES", 2 * 1024 * 1024 * 1024, 1), _int("PAGE_SIZE", 100, 1),
            _float("PAGE_FETCH_DELAY", 1, 0), _float("ITEM_DELAY", .5, 0), _float("PROGRESS_INTERVAL", 15, 1),
            _int("LEASE_SECONDS", 900, 30), _int("MAX_PAGES", 100000, 1),
            tuple(x.strip().lower() for x in os.getenv("MEDIA_HOSTS", "i.ibb.co,ibb.co,imgbb.com").split(",") if x.strip()))

    def ensure_paths(self) -> None:
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

SETTINGS = Settings.load()
BOT_TOKEN, ADMIN_IDS, DB_CHANNEL_ID = SETTINGS.bot_token, SETTINGS.admin_ids, SETTINGS.channel_id
IMGBB_EMAIL, IMGBB_PASSWORD = SETTINGS.imgbb_email, SETTINGS.imgbb_password
DB_FILE, TEMP_DIR = str(SETTINGS.db_file), str(SETTINGS.temp_dir)
REQUEST_TIMEOUT, TELEGRAM_TIMEOUT = SETTINGS.timeout, SETTINGS.telegram_timeout
MAX_RETRIES, MAX_DOWNLOAD_BYTES = SETTINGS.retries, SETTINGS.max_download_bytes
PAGE_SIZE, PAGE_FETCH_DELAY, RATE_LIMIT_DELAY = SETTINGS.page_size, SETTINGS.page_delay, SETTINGS.item_delay
PROGRESS_INTERVAL, LEASE_SECONDS, MAX_PAGES = SETTINGS.progress_interval, SETTINGS.lease_seconds, SETTINGS.max_pages
MEDIA_HOSTS = SETTINGS.media_hosts
HEADERS = {"User-Agent": "IBBDownloader/2.0", "Accept-Language": "en-US,en;q=0.9"}
