"""Incremental, resumable ImgBB profile scraper with validated media URLs."""
from __future__ import annotations
import json
import logging, random, re, time
from dataclasses import dataclass
from html import unescape
from urllib.parse import unquote, urlparse, urlunparse
import requests
from bs4 import BeautifulSoup
from config import HEADERS, IMGBB_EMAIL, IMGBB_PASSWORD, MAX_PAGES, MAX_RETRIES, MEDIA_HOSTS, PAGE_FETCH_DELAY, PAGE_SIZE, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)
LOGIN_URL = "https://imgbb.com/login"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PHOTO_EXT = {"jpg", "jpeg", "png", "webp", "bmp"}
VIDEO_EXT = {"mp4", "webm", "mov", "avi", "mkv", "m4v", "3gp"}

class ScrapeError(RuntimeError):
    """A non-retryable profile or response error."""

@dataclass(frozen=True)
class MediaItem:
    page_url: str
    direct_url: str
    filename: str
    media_type: str

def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

def media_type(filename: str) -> str:
    ext = _extension(filename)
    if ext == "gif": return "gif"
    if ext in PHOTO_EXT: return "photo"
    if ext in VIDEO_EXT: return "video"
    return "document"

def username_from_url(url: str) -> str:
    """Return a safe username from either supported ImgBB profile URL form."""
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("https", "http") or not host:
        raise ValueError("Profile URL must be http(s)")
    if host == "ibb.co" and parsed.path.startswith("/user/"):
        name = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else ""
    elif host.endswith(".imgbb.com") and host.count(".") == 2:
        name = host.split(".")[0]
    else:
        raise ValueError("Use https://USERNAME.imgbb.com/ or https://ibb.co/user/USERNAME")
    if not USERNAME_RE.fullmatch(name):
        raise ValueError("Invalid ImgBB username")
    return name

def normalize_media_url(raw: str) -> str | None:
    """Only allow HTTPS media URLs from approved hosts."""
    raw = (raw or "").strip()
    if raw.startswith("//"): raw = "https:" + raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or not any(host == h or host.endswith("." + h) for h in MEDIA_HOSTS):
        return None
    return urlunparse(("https", parsed.netloc, parsed.path, "", parsed.query, ""))

def _retryable(status: int) -> bool:
    return status in (408, 425, 429) or status >= 500

def _get(session, url, **kwargs):
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
            if _retryable(response.status_code) and attempt < MAX_RETRIES:
                delay = float(response.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(delay, 60) * random.uniform(1, 1.3) + PAGE_FETCH_DELAY)
                continue
            response.raise_for_status()
            return response
        except (requests.ConnectionError, requests.Timeout):
            if attempt == MAX_RETRIES: raise
            time.sleep(min(2 ** attempt, 60) * random.uniform(1, 1.3))
    raise ScrapeError("Request retries exhausted")

def create_session() -> requests.Session:
    """Create an ImgBB session and authenticate when credentials are configured.

    ImgBB uses a session cookie plus a page-specific ``auth_token``. A 200
    response from the login POST is not sufficient proof of authentication, so
    verify the returned page contains the signed-in account marker.
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    if not IMGBB_EMAIL or not IMGBB_PASSWORD:
        logger.warning("IMGBB_EMAIL/IMGBB_PASSWORD are not configured; private profiles cannot be read")
        return session

    login_page = _get(session, LOGIN_URL)
    soup = BeautifulSoup(login_page.text, "html.parser")
    token_input = soup.find("input", {"name": "auth_token"})
    auth_token = token_input.get("value", "") if token_input else ""
    if not auth_token:
        raise ScrapeError("ImgBB login page did not provide an auth token")

    response = session.post(
        LOGIN_URL,
        data={
            "auth_token": auth_token,
            "login-subject": IMGBB_EMAIL,
            "password": IMGBB_PASSWORD,
        },
        headers={"Referer": LOGIN_URL},
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
    )
    if response.status_code >= 400:
        raise ScrapeError(f"ImgBB login rejected the request (HTTP {response.status_code})")

    signed_in = BeautifulSoup(response.text, "html.parser").select_one("#top-bar-user")
    if not signed_in and "logout" not in response.text.lower() and "sign out" not in response.text.lower():
        raise ScrapeError("ImgBB login failed; check the email, password, and account login method")
    logger.info("ImgBB authentication succeeded")
    return session

def _to_item(record: dict) -> MediaItem | None:
    if not isinstance(record, dict): return None
    video = record.get("video") if isinstance(record.get("video"), dict) else {}
    image = record.get("image") if isinstance(record.get("image"), dict) else {}
    direct = normalize_media_url(video.get("url") or image.get("url") or record.get("display_url") or "")
    if not direct: return None
    filename = image.get("filename") or direct.split("?")[0].rsplit("/", 1)[-1] or "media"
    page = normalize_media_url(record.get("url_viewer") or direct) or direct
    return MediaItem(page, direct, filename, media_type(filename))

def _profile_auth_token(session: requests.Session, username: str) -> str:
    """Get the auth token embedded in the authenticated profile page."""
    cached = getattr(session, "_imgbb_profile_token", "")
    if cached:
        return cached
    profile_url = f"https://{username}.imgbb.com/"
    response = _get(session, profile_url)
    soup = BeautifulSoup(response.text, "html.parser")
    token_input = soup.find("input", {"name": "auth_token"})
    token = token_input.get("value", "") if token_input else ""
    if not token:
        match = re.search(r"(?:auth_token|authToken)[\"']?\s*[:=]\s*[\"']([^\"']+)", response.text)
        token = match.group(1) if match else ""
    if not token:
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        if "sign in" in title.lower() or "login" in response.url.lower():
            raise ScrapeError("ImgBB credentials were not accepted for this profile")
        raise ScrapeError("ImgBB profile page did not provide an auth token")
    setattr(session, "_imgbb_profile_token", token)
    return token


def _records_from_listing(data: dict) -> list[dict]:
    """Decode ImgBB's current JSON response, which embeds HTML listing cards."""
    records = data.get("images")
    if records is None:
        records = data.get("data")
    if isinstance(records, list):
        return [record for record in records if isinstance(record, dict)]

    markup = data.get("html")
    if not isinstance(markup, str):
        raise ScrapeError("Unexpected ImgBB response: missing images or html")
    soup = BeautifulSoup(markup, "html.parser")
    decoded = []
    for card in soup.select(".list-item[data-object]"):
        raw = unquote(unescape(card.get("data-object", "")))
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("Skipping malformed ImgBB listing item")
            continue
        if isinstance(record, dict):
            decoded.append(record)
    return decoded


def fetch_page(session, username: str, page: int, seek: str | None) -> tuple[list[MediaItem], str | None, bool]:
    """Return (items, next_seek, complete) for one authenticated ImgBB page."""
    token = _profile_auth_token(session, username)
    params = {
        "auth_token": token,
        "pathname": "/",
        "action": "list",
        "page": page,
        "per_page": PAGE_SIZE,
    }
    if seek:
        params["seek"] = seek
    response = _get(session, f"https://{username}.imgbb.com/json", params=params)
    try:
        data = response.json()
    except ValueError as exc:
        raise ScrapeError("ImgBB returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise ScrapeError("Unexpected ImgBB response")

    records = _records_from_listing(data)
    items, seen = [], set()
    for record in records:
        item = _to_item(record)
        if item and item.direct_url not in seen:
            seen.add(item.direct_url)
            items.append(item)

    # ImgBB profile pages currently return a fixed-size batch (often 32
    # records) even when ``per_page`` is larger.  Some responses expose a
    # cursor, while others paginate only through the ``page`` parameter.  Do
    # not manufacture a cursor from the last record: that makes the next
    # request use an unsupported ``seek`` value and can stop after page one.
    next_seek = data.get("seekEnd") or data.get("seek_end") or data.get("next_seek")
    if next_seek is None:
        next_seek = data.get("nextSeek") or data.get("next_cursor")
    complete = not records
    return items, str(next_seek) if next_seek else None, complete

def scrape_profile(profile_url: str) -> tuple[list[MediaItem], str]:
    """Compatibility helper; the worker uses fetch_page incrementally."""
    username = username_from_url(profile_url); session = create_session(); result=[]; page=1; seek=None
    while page <= MAX_PAGES:
        items, next_seek, complete = fetch_page(session, username, page, seek)
        result.extend(items)
        if complete or (next_seek and next_seek == seek): break
        seek=next_seek; page += 1; time.sleep(PAGE_FETCH_DELAY)
    return result, username
