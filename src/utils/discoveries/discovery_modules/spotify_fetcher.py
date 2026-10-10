import re
import time
from urllib.parse import quote

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config.constants import SPELLING_CHECK_THRESHOLD
from utils.common.debug import flog, slog
from utils.common.selenium_sessions import get_global_driver
from utils.common.text_utils import is_blacklisted_album, similarity
from utils.discoveries.discovery_result import DiscoveryResult, YearDiscoveryResult

# Scrapes the public open.spotify.com web player instead of calling the
# official Web API, so no app registration / client_id-client_secret is
# needed. Searching and viewing track/album pages on open.spotify.com works
# without being logged in. Trade-off: this depends on undocumented frontend
# markup (data-testid attributes) rather than a stable API contract, so it's
# more likely to break if Spotify changes their web player.
MODULE_NAME = "Spotify fetcher"

# Last page that failed to load, overwritten each time - to see whether it was
# a cookie wall, an empty search or markup that changed.
DEBUG_SNAPSHOT_FILE = "debug_spotify.html"


def _save_snapshot(driver) -> None:
    try:
        with open(DEBUG_SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception:
        pass


def _search_url(text: str, kind: str) -> str:
    # The query is a *path* segment here, so "/" must be escaped too (quote()
    # leaves it alone by default): "Jidyszland / Yiddishland" otherwise splits
    # the path and the search page never loads.
    return f"https://open.spotify.com/search/{quote(text, safe='')}/{kind}"


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

    search_url = _search_url(f"{artist} {title}", "tracks")
    slog(search_url, priority=1)
    driver.get(search_url)
    time.sleep(2)

    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, '[data-testid="tracklist-row"]'))
        )
    except Exception:
        flog("[Spotify] No search results found")
        return None

    soup = BeautifulSoup(driver.page_source, "html.parser")
    match = _find_matching_track(soup, artist, title)
    if not match:
        flog("[Spotify] No matching track in search results")
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
        flog("[Spotify] Track page didn't load")
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


def _find_matching_album(soup: BeautifulSoup, artist: str, album: str):
    """Scan the '/albums' search result cards and return the href of the
    best-matching album, or None if nothing clears the similarity threshold
    for both album title and artist."""
    best = None
    best_score = 0.0

    for card in soup.select('[data-encore-id="card"]'):
        album_link = card.select_one('a[href^="/album/"]')
        title_el = card.select_one('[data-encore-id="cardTitle"]')
        if not album_link or not title_el:
            continue
        card_title = title_el.get("title") or title_el.get_text(strip=True)

        card_artists = [a.get_text(strip=True) for a in card.select('a[href^="/artist/"]')]
        if not card_artists:
            continue

        title_sim = similarity(album, card_title)
        artist_sim = max(similarity(artist, a) for a in card_artists)
        if title_sim < SPELLING_CHECK_THRESHOLD or artist_sim < SPELLING_CHECK_THRESHOLD:
            continue

        score = min(title_sim, artist_sim)
        if score > best_score:
            best_score = score
            best = album_link["href"]

    return best


def _load(driver, url: str, wait_selector: str) -> BeautifulSoup | None:
    driver.get(url)
    time.sleep(2)
    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, wait_selector))
        )
    except Exception:
        flog(f"[Spotify] Nothing matching {wait_selector} on {url} (page saved to {DEBUG_SNAPSHOT_FILE})")
        _save_snapshot(driver)
        return None
    return BeautifulSoup(driver.page_source, "html.parser")


def _extract_year_from_page(soup: BeautifulSoup, page_testid: str) -> YearDiscoveryResult | None:
    """Read the header of a track or album page. Its release-date is the
    release date of the track's album (for a single, the single itself)."""
    section = soup.select_one(f'[data-testid="{page_testid}"]')
    if not section:
        return None
    date_el = section.select_one('[data-testid="release-date"]')
    match = re.search(r"\d{4}", date_el.get_text(strip=True)) if date_el else None
    if not match:
        return None

    title_el = section.select_one('[data-testid="entityTitle"]')
    artist_el = section.select_one('a[data-testid="creator-link"]')
    return YearDiscoveryResult(
        year=int(match.group(0)),
        matched_title=title_el.get_text(strip=True) if title_el else None,
        matched_artist=artist_el.get_text(strip=True) if artist_el else None,
    )


def get_release_year(artist: str, query: str, is_single: bool) -> YearDiscoveryResult | None:
    """is_single=True: query is a song title, read off its track page.
    is_single=False: query is an album title, searched among albums and read
    off the album page."""
    driver = get_global_driver()
    if driver is None:
        slog("[Spotify] No driver open. Call open_global_driver() first.")
        return None

    kind, row_selector = ("tracks", '[data-testid="tracklist-row"]') if is_single else ("albums", '[data-encore-id="card"]')
    search_url = _search_url(f"{artist} {query}", kind)
    slog(search_url, priority=1)
    soup = _load(driver, search_url, row_selector)
    if soup is None:
        return None

    if is_single:
        match = _find_matching_track(soup, artist, query)
        href = match[0] if match else None
        page_testid = "track-page"
    else:
        href = _find_matching_album(soup, artist, query)
        page_testid = "album-page"
    if not href:
        flog(f"[Spotify] No matching {kind} in search results")
        return None

    page = _load(driver, f"https://open.spotify.com{href}", f'[data-testid="{page_testid}"]')
    if page is None:
        return None
    return _extract_year_from_page(page, page_testid)


# open.spotify.com/album/<id> or /track/<id>, optionally with a locale prefix
# ("/intl-pl/") and a share-tracking query ("?si=...").
_SPOTIFY_URL_RE = re.compile(
    r"^(?:https?://)?open\.spotify\.com/(?:intl-[a-z-]+/)?(album|track)/([A-Za-z0-9]+)", re.IGNORECASE
)


def normalize_spotify_url(url: str) -> str | None:
    """Canonical "https://open.spotify.com/<album|track>/<id>", or None if
    `url` isn't a Spotify album/track link."""
    match = _SPOTIFY_URL_RE.match((url or "").strip())
    if not match:
        return None
    return f"https://open.spotify.com/{match.group(1).lower()}/{match.group(2)}"


def get_release_year_from_url(url: str) -> YearDiscoveryResult | None:
    """Release year read straight off a known album/track page (a song's
    stored spotify_url), skipping the search entirely."""
    url = normalize_spotify_url(url)
    if url is None:
        return None
    driver = get_global_driver()
    if driver is None:
        slog("[Spotify] No driver open. Call open_global_driver() first.")
        return None
    page_testid = "album-page" if "/album/" in url else "track-page"
    page = _load(driver, url, f'[data-testid="{page_testid}"]')
    if page is None:
        return None
    return _extract_year_from_page(page, page_testid)
