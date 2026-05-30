"""
scraper.py — Scrapes an imgbb.com user profile with login support.

Logs in with email+password, then scrapes all media using the JSON API.
Supports both:
  - https://username.imgbb.com/
  - https://ibb.co/user/username
"""

import re
import time
import logging
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from config import HEADERS, PAGE_FETCH_DELAY, REQUEST_TIMEOUT, MAX_RETRIES, IMGBB_EMAIL, IMGBB_PASSWORD

logger = logging.getLogger(__name__)

LOGIN_URL = "https://imgbb.com/login"
HOME_URL  = "https://imgbb.com/"


@dataclass
class MediaItem:
    page_url: str
    direct_url: str
    filename: str
    media_type: str


def _media_type_from_ext(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("mp4", "webm", "mov", "avi"):
        return "video"
    elif ext == "gif":
        return "gif"
    elif ext in ("jpg", "jpeg", "png", "webp", "bmp"):
        return "photo"
    return "document"


def _extract_username(profile_url: str) -> str:
    match = re.search(r"https?://([^.]+)\.imgbb\.com", profile_url)
    if match:
        return match.group(1)
    match = re.search(r"ibb\.co/user/([^/?#]+)", profile_url)
    if match:
        return match.group(1)
    raise ValueError(f"Could not parse username from URL: {profile_url}")


def _create_session() -> requests.Session:
    """Create a session and log in to imgbb."""
    session = requests.Session()
    session.headers.update(HEADERS)

    if not IMGBB_EMAIL or not IMGBB_PASSWORD:
        logger.info("No imgbb credentials set — trying without login.")
        return session

    # Step 1: GET login page to grab the auth_token
    logger.info("Logging in to imgbb as %s...", IMGBB_EMAIL)
    resp = session.get(LOGIN_URL, timeout=REQUEST_TIMEOUT)
    soup = BeautifulSoup(resp.text, "html.parser")

    auth_token = ""
    token_input = soup.find("input", {"name": "auth_token"})
    if token_input:
        auth_token = token_input.get("value", "")

    # Step 2: POST login credentials
    payload = {
        "auth_token": auth_token,
        "login-subject": IMGBB_EMAIL,
        "password": IMGBB_PASSWORD,
        "action": "login",
    }
    login_resp = session.post(
        LOGIN_URL,
        data=payload,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
    )

    # Check if login succeeded
    if "logout" in login_resp.text.lower() or "sign out" in login_resp.text.lower():
        logger.info("Login successful!")
    elif login_resp.url and "imgbb.com" in login_resp.url and "login" not in login_resp.url:
        logger.info("Login successful (redirected to home)!")
    else:
        logger.warning("Login may have failed — check credentials in config.")

    return session


def _fetch_all_media(session: requests.Session, username: str) -> list[MediaItem]:
    """
    Use imgbb's JSON endpoint to get all images for a user profile.
    Falls back to HTML parsing if JSON API fails.
    """
    items: list[MediaItem] = []
    page = 1
    seek = None

    base_url = f"https://{username}.imgbb.com/json"

    while True:
        params = {"page": page, "per_page": 100}
        if seek:
            params["seek"] = seek

        logger.info("Fetching page %d for user %s", page, username)

        try:
            resp = session.get(base_url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("JSON API failed at page %d: %s", page, e)
            break

        images = data.get("images") or data.get("data") or []
        if not images:
            logger.info("No more images at page %d", page)
            break

        for img in images:
            video = img.get("video") or {}
            image = img.get("image") or {}

            if video.get("url"):
                direct_url = video["url"]
            elif image.get("url"):
                direct_url = image["url"]
            else:
                direct_url = img.get("display_url") or img.get("url_viewer", "")
                if not direct_url:
                    continue

            filename = (
                image.get("filename")
                or direct_url.split("/")[-1].split("?")[0]
                or "file.jpg"
            )
            page_url = img.get("url_viewer") or direct_url

            items.append(MediaItem(
                page_url=page_url,
                direct_url=direct_url,
                filename=filename,
                media_type=_media_type_from_ext(filename),
            ))

        logger.info("Page %d — %d items (total: %d)", page, len(images), len(items))

        # Check pagination
        total_pages = data.get("total_pages") or data.get("pages_count") or 1
        current_page = data.get("current_page") or page
        if int(current_page) >= int(total_pages) or len(images) < 10:
            break

        if images:
            last = images[-1]
            seek = last.get("id_encoded") or last.get("id") or None

        page += 1
        time.sleep(PAGE_FETCH_DELAY)

    # If JSON API returned nothing, try HTML fallback
    if not items:
        logger.info("JSON API returned nothing — trying HTML fallback...")
        items = _fetch_via_html(session, username)

    return items


def _fetch_via_html(session: requests.Session, username: str) -> list[MediaItem]:
    """HTML fallback scraper."""
    items: list[MediaItem] = []
    page = 1
    seen: set[str] = set()

    while True:
        url = f"https://{username}.imgbb.com/?page={page}"
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("HTML fetch failed at page %d: %s", page, e)
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        found = 0

        for a in soup.select("a.image-container, .list-item-image a"):
            href = a.get("href", "").strip()
            if not href or href in seen:
                continue
            seen.add(href)

            # Try to get direct URL from data attributes first
            img_tag = a.find("img")
            direct = (
                a.get("data-url") or
                a.get("data-src") or
                (img_tag.get("src", "") if img_tag else "")
            )

            # If still no direct URL, visit viewer page
            if not direct or "thumb" in direct:
                time.sleep(PAGE_FETCH_DELAY)
                try:
                    vresp = session.get(href, timeout=REQUEST_TIMEOUT)
                    vsoup = BeautifulSoup(vresp.text, "html.parser")
                    og = vsoup.find("meta", property="og:image")
                    direct = og["content"] if og else ""
                    if not direct:
                        dl = vsoup.select_one("a.btn-download, a[href*='i.ibb.co']")
                        direct = dl["href"] if dl else ""
                except Exception:
                    continue

            if not direct:
                continue

            direct = direct.strip()
            if direct.startswith("//"):
                direct = "https:" + direct

            filename = direct.split("?")[0].split("/")[-1] or "file.jpg"
            items.append(MediaItem(
                page_url=href,
                direct_url=direct,
                filename=filename,
                media_type=_media_type_from_ext(filename),
            ))
            found += 1

        logger.info("HTML page %d — found %d items", page, found)

        next_btn = soup.select_one(
            "a[data-pagination='next'], .pagination .next a, a[rel='next']"
        )
        if not next_btn or found == 0:
            break

        page += 1
        time.sleep(PAGE_FETCH_DELAY)

    return items


def scrape_profile(profile_url: str) -> tuple[list[MediaItem], str]:
    """
    Main entry point.
    Logs in, scrapes all media, returns (items, username).
    """
    username = _extract_username(profile_url)
    logger.info("Scraping imgbb profile: %s", username)

    session = _create_session()
    items = _fetch_all_media(session, username)

    logger.info("Total found for %s: %d", username, len(items))
    return items, username
