import time
from urllib.parse import quote

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config.constants import SPELLING_CHECK_THRESHOLD
from utils.common.debug import slog
from utils.common.selenium_sessions import get_global_driver
from utils.common.text_utils import is_blacklisted_album, similarity
from utils.discoveries.discovery_result import DiscoveryResult

# Scrapes the public music.youtube.com web player instead of calling the
# official YouTube Data API, so no app registration / API key is needed.
# Searching and browsing music.youtube.com works without being logged in.
# Trade-off: this depends on undocumented frontend markup (custom element
# names and href prefixes) rather than a stable API contract, so it's more
# likely to break if YouTube changes their web player.
MODULE_NAME = "YouTube fetcher"

# A fresh, cookie-less Selenium profile gets redirected to this consent
# interstitial the first time it visits any youtube.com page. Once rejected,
# the resulting cookie sticks for the rest of the shared driver's session.
_CONSENT_URL_MARKER = "consent.youtube.com"


def _reject_consent_if_present(driver) -> None:
    if _CONSENT_URL_MARKER not in driver.current_url:
        return

    slog("[YouTube] Consent interstitial shown, rejecting", priority=1)
    try:
        reject_button = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[aria-label="Reject all"]'))
        )
        reject_button.click()
    except Exception:
        slog("[YouTube] Couldn't find/click the consent 'Reject all' button")
        return

    WebDriverWait(driver, 8).until(
        lambda d: _CONSENT_URL_MARKER not in d.current_url
    )


def _find_matching_song(soup: BeautifulSoup, artist: str, title: str):
    """Scan the filtered 'Songs' search results and return (matched_title,
    matched_artist, album) for the best-matching row, or None if nothing
    clears the similarity threshold for both title and artist."""
    best = None
    best_score = 0.0

    for row in soup.select("ytmusic-responsive-list-item-renderer"):
        title_el = row.select_one(".title")
        if not title_el:
            continue
        row_title = title_el.get("title") or title_el.get_text(strip=True)

        row_artists = [a.get_text(strip=True) for a in row.select('a[href^="channel/"]')]
        if not row_artists:
            continue

        album_link = row.select_one('a[href^="browse/"]')
        if not album_link:
            continue
        album = album_link.get_text(strip=True)

        title_sim = similarity(title, row_title)
        artist_sim = max(similarity(artist, a) for a in row_artists)
        if title_sim < SPELLING_CHECK_THRESHOLD or artist_sim < SPELLING_CHECK_THRESHOLD:
            continue

        score = min(title_sim, artist_sim)
        if score > best_score:
            best_score = score
            best = (row_title, row_artists[0], album)

    return best


def get_album_name(artist: str, title: str) -> DiscoveryResult | None:
    driver = get_global_driver()
    if driver is None:
        slog("[YouTube] No driver open. Call open_global_driver() first.")
        return None

    query = quote(f"{artist} {title}")
    search_url = f"https://music.youtube.com/search?q={query}"
    slog(search_url, priority=1)
    driver.get(search_url)
    _reject_consent_if_present(driver)
    if driver.current_url != search_url:
        driver.get(search_url)
    time.sleep(2)

    try:
        songs_tab = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable((By.XPATH, "//ytmusic-chip-cloud-chip-renderer[.//yt-formatted-string[normalize-space(text())='Songs']]"))
        )
        songs_tab.click()
    except Exception:
        slog("[YouTube] No 'Songs' filter chip found")
        return None

    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, "ytmusic-shelf-renderer ytmusic-responsive-list-item-renderer")
            )
        )
    except Exception:
        slog("[YouTube] No song results found")
        return None

    soup = BeautifulSoup(driver.page_source, "html.parser")
    match = _find_matching_song(soup, artist, title)
    if not match:
        slog("[YouTube] No matching song in search results")
        return None

    matched_title, matched_artist, album = match
    if is_blacklisted_album(album):
        return None

    return DiscoveryResult(
        album=album,
        matched_title=matched_title,
        matched_artist=matched_artist,
    )
