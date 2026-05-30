"""
scraper.py — Scrapes an imgbb.com user profile and returns all media URLs.

Supports both formats:
  - https://username.imgbb.com/
  - https://ibb.co/user/username

Flow:
  1. Detect URL format and extract username
  2. Hit imgbb's JSON API endpoint to get all images (handles pagination)
  3. Return a list of MediaItem objects with direct download URLs
"""

import re
import time
import logging
from dataclasses import dataclass
from typing import Generator

import requests
from bs4 import BeautifulSoup

from config import HEADERS, PAGE_FETCH_DELAY, REQUEST_TIMEOUT, MAX_RETRIES

logger = logging.getLogger(__name__)


@dataclass
class MediaItem:
    page_url: str       # e.g. https://ibb.co/AbCdEfG
    direct_url: str     # actual file URL (i.ibb.co/...)
    filename: str       # derived filename
    media_type: str     # "photo", "gif", "video", "document"


def _get(url: str, retries: int = MAX_RETRIES, **kwargs) -> requests.Response | None:
    """GET with retries."""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            logger.warning("Attempt %d/%d failed for %s — %s", attempt, retries, url, e)
            if attempt < retries:
                time.sleep(PAGE_FETCH_DELAY * attempt)
    logger.error("All retries exhausted for %s", url)
    return None


def _extract_username(profile_url: str) -> str:
    """
    Extract username from either:
      https://username.imgbb.com/
      https://ibb.co/user/username
    """
    # subdomain format: username.imgbb.com
    match = re.search(r"https?://([^.]+)\.imgbb\.com", profile_url)
    if match:
        return match.group(1)

    # ibb.co/user/username format
    match = re.search(r"ibb\.co/user/([^/?#]+)", profile_url)
    if match:
        return match.group(1)

    raise ValueError(f"Could not parse username from URL: {profile_url}")


def _media_type_from_ext(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("mp4", "webm", "mov", "avi"):
        return "video"
    elif ext == "gif":
        return "gif"
    elif ext in ("jpg", "jpeg", "png", "webp", "bmp"):
        return "photo"
    return "document"


def _fetch_via_api(username: str) -> list[MediaItem]:
    """
    imgbb exposes a paginated JSON endpoint:
      https://<username>.imgbb.com/json?page=1&seek=<last_id>

    Each response contains an array of image objects with:
      - image.url      → direct image URL (i.ibb.co/...)
      - image.url_viewer → page URL
      - image.filename
      - image.thumb.url → thumbnail
      - video.url      → if it's a video
    """
    base_url = f"https://{username}.imgbb.com/json"
    items: list[MediaItem] = []
    page = 1
    seek = None

    while True:
        params = {"page": page, "per_page": 100}
        if seek:
            params["seek"] = seek

        logger.info("Fetching API page %d for user %s", page, username)
        resp = _get(base_url, params=params)
        if resp is None:
            logger.warning("API request failed at page %d", page)
            break

        try:
            data = resp.json()
        except Exception as e:
            logger.error("Failed to parse JSON response: %s", e)
            break

        images = data.get("images") or data.get("data") or []
        if not images:
            logger.info("No more images at page %d", page)
            break

        for img in images:
            # Try video first
            video = img.get("video") or {}
            image = img.get("image") or {}
            thumb = img.get("thumb") or {}

            if video.get("url"):
                direct_url = video["url"]
            elif image.get("url"):
                direct_url = image["url"]
            else:
                # fallback: construct from image id
                img_id = img.get("id_encoded") or img.get("id") or ""
                direct_url = img.get("url_viewer", "")
                if not direct_url:
                    continue

            filename = image.get("filename") or direct_url.split("/")[-1].split("?")[0]
            page_url = img.get("url_viewer") or img.get("display_url") or direct_url

            items.append(MediaItem(
                page_url=page_url,
                direct_url=direct_url,
                filename=filename,
                media_type=_media_type_from_ext(filename),
            ))

        logger.info("Page %d — got %d items (total so far: %d)", page, len(images), len(items))

        # Pagination: check if there are more pages
        has_more = (
            data.get("current_page") != data.get("total_pages")
            and len(images) >= 10
        )
        if not has_more:
            break

        # Use last image id as seek cursor if available
        if images:
            last = images[-1]
            seek = last.get("id_encoded") or last.get("id") or None

        page += 1
        time.sleep(PAGE_FETCH_DELAY)

    return items


def _fetch_via_html(username: str) -> list[MediaItem]:
    """
    Fallback: parse the HTML profile page if JSON API fails.
    Looks for og:image and data-src attributes.
    """
    items: list[MediaItem] = []
    page = 1
    seen: set[str] = set()

    while True:
        url = f"https://{username}.imgbb.com/?page={page}"
        logger.info("HTML fallback — fetching page %d", page)
        resp = _get(url)
        if resp is None:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        found = 0

        # Each image card typically has a link to the viewer page
        for a in soup.select("a.image-container, .list-item-image a, a[href*='ibb.co']"):
            href = a.get("href", "").strip()
            if not href or href in seen:
                continue
            seen.add(href)

            # Get direct url from data attribute if available
            direct = (
                a.get("data-url") or
                a.get("data-src") or
                (a.find("img") or {}).get("src", "") if a.find("img") else ""
            )

            if not direct:
                # visit viewer page to get direct URL
                time.sleep(PAGE_FETCH_DELAY)
                vresp = _get(href)
                if vresp:
                    vsoup = BeautifulSoup(vresp.text, "html.parser")
                    og = vsoup.find("meta", property="og:image")
                    direct = og["content"] if og else ""
                    if not direct:
                        dl = vsoup.select_one("a.btn-download, a[href*='i.ibb.co']")
                        direct = dl["href"] if dl else ""

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

        next_btn = soup.select_one("a[data-pagination='next'], .pagination .next a, a[rel='next']")
        if not next_btn or found == 0:
            break

        page += 1
        time.sleep(PAGE_FETCH_DELAY)

    return items


def scrape_profile(profile_url: str) -> tuple[list[MediaItem], str]:
    """
    Main entry point.
    Returns (list_of_MediaItems, username).
    Tries JSON API first, falls back to HTML scraping.
    """
    username = _extract_username(profile_url)
    logger.info("Starting scrape for imgbb user: %s", username)

    # Try JSON API first (faster, more reliable)
    items = _fetch_via_api(username)

    if not items:
        logger.info("JSON API returned nothing, trying HTML fallback...")
        items = _fetch_via_html(username)

    logger.info("Total media items found for %s: %d", username, len(items))
    return items, username
