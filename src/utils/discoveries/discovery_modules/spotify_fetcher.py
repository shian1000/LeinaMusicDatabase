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

# Scrapes the public open.spotify.com web player instead of calling the
# official Web API, so no app registration / client_id-client_secret is
# needed. Searching and viewing track/album pages on open.spotify.com works
# without being logged in. Trade-off: this depends on undocumented frontend
# markup (data-testid attributes) rather than a stable API contract, so it's
# more likely to break if Spotify changes their web player.
MODULE_NAME = "Spotify fetcher"


def _find_matching_track(soup: BeautifulSoup, artist: str, title: str):
    """Scan the '/tracks' search results grid and return (href, matched_title,
    matched_artist) for the best-matching row, or None if nothing clears the
    similarity threshold for both title and artist."""
    best = None
    best_score = 0.0

    for row in soup.select('[data-testid="tracklist-row"]'):
        track_link = row.select_one('a[href^="/track/"]')
        if not track_link:
            continue
        row_title = track_link.get_text(strip=True)

        row_artists = [a.get_text(strip=True) for a in row.select('a[href^="/artist/"]')]
        if not row_artists:
            continue

        title_sim = similarity(title, row_title)
        artist_sim = max(similarity(artist, a) for a in row_artists)
        if title_sim < SPELLING_CHECK_THRESHOLD or artist_sim < SPELLING_CHECK_THRESHOLD:
            continue

        score = min(title_sim, artist_sim)
        if score > best_score:
            best_score = score
            best = (track_link["href"], row_title, row_artists[0])

    return best


def _extract_album_from_track_page(soup: BeautifulSoup):
    """Returns (album, matched_title, matched_artist) or None."""
    section = soup.select_one('[data-testid="track-page"]')
    if not section:
        return None

    album_link = section.select_one('a[href^="/album/"]')
    if not album_link:
        return None
    album = album_link.get_text(strip=True)

    title_el = section.select_one('[data-testid="entityTitle"]')
    matched_title = title_el.get_text(strip=True) if title_el else None

    artist_el = section.select_one('a[data-testid="creator-link"]')
    matched_artist = artist_el.get_text(strip=True) if artist_el else None

    return album, matched_title, matched_artist


def get_album_name(artist: str, title: str) -> DiscoveryResult | None:
    driver = get_global_driver()
    if driver is None:
        slog("[Spotify] No driver open. Call open_global_driver() first.")
        return None

    query = quote(f"{artist} {title}")
    search_url = f"https://open.spotify.com/search/{query}/tracks"
    slog(search_url, priority=1)
    driver.get(search_url)
    time.sleep(2)

    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, '[data-testid="tracklist-row"]'))
        )
    except Exception:
        slog("[Spotify] No search results found")
        return None

    soup = BeautifulSoup(driver.page_source, "html.parser")
    match = _find_matching_track(soup, artist, title)
    if not match:
        slog("[Spotify] No matching track in search results")
        return None

    href, row_title, row_artist = match
    track_url = f"https://open.spotify.com{href}"
    slog(f"[Spotify] Found matching track: {track_url}", priority=1)
    driver.get(track_url)
    time.sleep(2)

    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[data-testid="track-page"]'))
        )
    except Exception:
        slog("[Spotify] Track page didn't load")
        return None

    track_soup = BeautifulSoup(driver.page_source, "html.parser")
    extracted = _extract_album_from_track_page(track_soup)
    if not extracted:
        return None

    album, matched_title, matched_artist = extracted
    if is_blacklisted_album(album):
        return None

    return DiscoveryResult(
        album=album,
        matched_title=matched_title or row_title,
        matched_artist=matched_artist or row_artist,
    )
