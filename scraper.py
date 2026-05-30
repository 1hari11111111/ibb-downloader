"""
scraper.py — Scrapes an IBB user profile and returns all media URLs.

Flow:
  1. Fetch the profile page(s) at ibb.co/user/<username>
  2. Collect every individual image-page link (e.g. ibb.co/AbCdEfG)
  3. Visit each image page and extract the direct media URL
  4. Return a list of MediaItem objects
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

IBB_BASE = "https://ibb.co"


@dataclass
class MediaItem:
    page_url: str       # e.g. https://ibb.co/AbCdEfG
    direct_url: str     # actual file URL
    filename: str       # derived filename
    media_type: str     # "photo", "gif", "video", "document"


def _get(url: str, retries: int = MAX_RETRIES) -> requests.Response | None:
    """GET with retries and a small delay."""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            logger.warning("Attempt %d/%d failed for %s — %s", attempt, retries, url, e)
            if attempt < retries:
                time.sleep(PAGE_FETCH_DELAY * attempt)
    logger.error("All retries exhausted for %s", url)
    return None


def _extract_username(profile_url: str) -> str:
    """Pull the username out of a profile URL."""
    match = re.search(r"ibb\.co/user/([^/?#]+)", profile_url)
    if not match:
        raise ValueError(f"Could not parse username from URL: {profile_url}")
    return match.group(1)


def _image_page_links(profile_url: str) -> Generator[str, None, None]:
    """
    Yield every individual image-page URL from a profile,
    following IBB's pagination automatically.
    """
    # Normalise to the /uploads tab which lists all user-uploaded media
    username = _extract_username(profile_url)
    base = f"{IBB_BASE}/user/{username}/uploads"

    page = 1
    seen: set[str] = set()

    while True:
        url = f"{base}?page={page}"
        logger.info("Fetching profile page %d — %s", page, url)
        resp = _get(url)
        if resp is None:
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        # IBB wraps each thumbnail in an <a> inside .list-item-image
        links_found = 0
        for a_tag in soup.select("a.image-container"):
            href = a_tag.get("href", "")
            if not href:
                continue
            full = href if href.startswith("http") else IBB_BASE + href
            if full not in seen:
                seen.add(full)
                links_found += 1
                yield full

        # Also try the viewer links pattern
        for a_tag in soup.select(".list-item-image a"):
            href = a_tag.get("href", "")
            if not href:
                continue
            full = href if href.startswith("http") else IBB_BASE + href
            if full not in seen and "ibb.co" in full:
                seen.add(full)
                links_found += 1
                yield full

        logger.info("Page %d — found %d new links", page, links_found)

        # Check if there's a next page
        next_btn = soup.select_one("a[data-pagination='next']") or \
                   soup.select_one(".pagination .next a") or \
                   soup.select_one("a[rel='next']")
        if not next_btn or links_found == 0:
            break

        page += 1
        time.sleep(PAGE_FETCH_DELAY)


def _extract_direct_url(image_page_url: str) -> MediaItem | None:
    """
    Visit a single IBB image page and extract the direct media URL.
    IBB puts the full-size URL in:
      - og:image meta tag
      - the download link
      - the viewer image src
    """
    resp = _get(image_page_url)
    if resp is None:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    direct_url = None

    # 1. Try the download button link (most reliable)
    dl = soup.select_one("a.btn-download") or \
         soup.select_one("a[href*='i.ibb.co']")
    if dl:
        direct_url = dl.get("href", "")

    # 2. Fall back to og:image
    if not direct_url:
        og = soup.find("meta", property="og:image")
        if og:
            direct_url = og.get("content", "")

    # 3. Fall back to the main viewer image
    if not direct_url:
        img = soup.select_one("#image-viewer-container img") or \
              soup.select_one(".viewer-image img") or \
              soup.select_one("img#image-id")
        if img:
            direct_url = img.get("src", "")

    if not direct_url:
        logger.warning("Could not find direct URL on page: %s", image_page_url)
        return None

    # Clean up URL
    direct_url = direct_url.strip()
    if direct_url.startswith("//"):
        direct_url = "https:" + direct_url

    # Derive filename
    filename = direct_url.split("?")[0].split("/")[-1]
    if not filename:
        filename = image_page_url.split("/")[-1] + ".jpg"

    # Determine media type
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("mp4", "webm", "mov", "avi"):
        media_type = "video"
    elif ext == "gif":
        media_type = "gif"
    elif ext in ("jpg", "jpeg", "png", "webp", "bmp"):
        media_type = "photo"
    else:
        media_type = "document"

    return MediaItem(
        page_url=image_page_url,
        direct_url=direct_url,
        filename=filename,
        media_type=media_type,
    )


def scrape_profile(profile_url: str) -> tuple[list[MediaItem], str]:
    """
    Main entry point.
    Returns (list_of_MediaItems, username).
    Collects all image page links first, then resolves each to a direct URL.
    """
    username = _extract_username(profile_url)
    logger.info("Starting scrape for user: %s", username)

    page_links = list(_image_page_links(profile_url))
    logger.info("Found %d image pages for user %s", len(page_links), username)

    items: list[MediaItem] = []
    for i, link in enumerate(page_links, 1):
        logger.info("Resolving %d/%d — %s", i, len(page_links), link)
        item = _extract_direct_url(link)
        if item:
            items.append(item)
        time.sleep(PAGE_FETCH_DELAY)

    logger.info("Resolved %d media items for user %s", len(items), username)
    return items, username
