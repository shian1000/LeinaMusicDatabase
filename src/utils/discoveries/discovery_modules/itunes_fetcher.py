from bs4 import BeautifulSoup
from utils.common.selenium_sessions import get_global_driver
import time
from utils.common.debug import slog
from urllib.parse import quote
from utils.common.text_utils import is_blacklisted_album, similarity
from utils.discoveries.discovery_result import DiscoveryResult
from config.constants import SPELLING_CHECK_THRESHOLD

MODULE_NAME = "Itunes Fetches"

# Apple Music appends this to the product title of releases that only ever
# came out as a single (no parent album) -- e.g. "Say So (feat. Nicki
# Minaj) - Single". It's the only place that fact is exposed on the page.
SINGLE_TITLE_SUFFIX = " - single"


def extract_from_itunes_soup(song_soup, artist, song):
    """Returns (album, matched_title, matched_artist) or None.

    Apple Music's web player no longer ships the schema.org JSON-LD tag this
    used to parse -- it's been rebuilt around Svelte components identified by
    stable `data-testid` attributes, which is what this reads instead.
    """
    title_tag = song_soup.select_one('[data-testid="non-editable-product-title"]')
    if not title_tag:
        return None
    album = title_tag.get_text(strip=True)

    # 1. Verify artist. product-subtitles can list several artists (e.g. a
    # remix credited to two acts) as separate <a> tags; check the query
    # against all of them but, like the old JSON-LD's byArtist[0], only
    # report the first as matched_artist -- comparing against the joined
    # text would tank the similarity score discoveries_manager computes.
    artist_tags = song_soup.select('[data-testid="product-subtitles"] a')
    artist_names = [a.get_text(strip=True) for a in artist_tags]
    if not any(artist.lower() in name.lower() for name in artist_names):
        slog(f"Artist mismatch: expected '{artist}', found {artist_names}")
        return None
    matched_artist = artist_names[0] if artist_names else ""

    # 2. Verify song title. The page can be a multi-track album, so check
    # every tracklist row rather than assuming the first one is ours.
    # Apple now bakes "(feat. X)" credits straight into the visible track
    # title (the old JSON-LD kept them out of `audio.name`), so compare
    # against the part before the parenthetical -- same convention
    # discover_album_name() already uses to clean the query itself.
    song_query = song.lower()
    matched_title = None
    for track_tag in song_soup.select('[data-testid="track-title"]'):
        track_title = track_tag.get_text(strip=True)
        track_title_cln = track_title.split("(")[0].strip()
        if similarity(song_query, track_title_cln.lower()) > SPELLING_CHECK_THRESHOLD:
            matched_title = track_title_cln
            break
    if not matched_title:
        slog(f"Song mismatch: expected '{song}', no tracklist title matched", priority=1)
        return None

    # 3. A single-only release reports no real album -- use the same
    # "Singles" sentinel wikipedia_fetcher already uses for this case, so
    # discoveries_manager and the rest of the pipeline need no changes.
    if album.lower().endswith(SINGLE_TITLE_SUFFIX):
        album = "Singles"

    if not album:
        return None

    slog(f"Matched: {artist} - {matched_title} ({album})")
    return album, matched_title, matched_artist


def _iter_song_candidates(soup):
    """Yield (title, href) for every song row in the search-results page.

    Apple Music has been observed serving two different search-results
    layouts (same URL, same query) depending on session/geo state: a
    dedicated "Songs" section (`track-lockup`, what a geo/storefront
    mismatch -- e.g. scraping "/us/..." from a non-US IP -- currently
    renders) and a mixed "Top Results" list where a row's type is only
    exposed via its subtitle text ("Song · Artist" vs "Album · Artist" /
    "Artist" / "Music Video · Artist"). Both are handled so the fetcher
    doesn't silently go dark if Apple serves the other one on a given run.
    """
    for row in soup.select('[data-testid="track-lockup"]'):
        title_tag = row.select_one('[data-testid="track-lockup-title"]')
        link = row.select_one('a[data-testid="click-action"]')
        if title_tag and link and link.get("href"):
            yield title_tag.get_text(strip=True), link["href"]

    for row in soup.select('div[data-testid="top-search-list-result"]'):
        subtitle = row.select_one('[data-testid="top-search-list-result-subtitle"]')
        if not subtitle or not subtitle.get_text(strip=True).startswith("Song"):
            continue
        title_link = row.select_one(
            '[data-testid="top-search-list-result-title"] a[data-testid="click-action"]'
        )
        if title_link and title_link.get("href"):
            yield title_link.get_text(strip=True), title_link["href"]


def _find_song_link(soup, song: str) -> str | None:
    """Scan the search-results page for a song row resembling `song` and
    return its href, or None. Split out from get_album_name() for testing
    against a static fixture, no Selenium involved."""
    song_query = song.lower()
    for title, href in _iter_song_candidates(soup):
        song_on_the_page = title.lower()
        slog(song_query, priority=1)
        slog(song_on_the_page, priority=1)
        if similarity(song_query, song_on_the_page) > SPELLING_CHECK_THRESHOLD:
            slog("Found", priority=1)
            return href
    return None


def get_album_name(artist: str, song: str) -> str | None:
    query = quote(f"{artist} {song}")
    url = f"https://music.apple.com/us/search?term={query}"

    slog(url, priority=1)

    #Open itunes and look for the song:
    driver = get_global_driver()
    if driver is None:
        slog("[iTunes] No driver open. Call open_global_driver() first.")
        return None
    driver.get(url)
    time.sleep(2)

    soup = BeautifulSoup(driver.page_source, "html.parser")

    #Open the link of the song:
    song_link = _find_song_link(soup, song)
    if not song_link:
        return None

    slog(f"Found song link: {song_link}", priority=1)
    driver.get(song_link)
    time.sleep(2)

    song_soup = BeautifulSoup(driver.page_source, "html.parser")
    extracted = extract_from_itunes_soup(song_soup, artist, song)
    if not extracted:
        return None

    album_name, matched_title, matched_artist = extracted
    if is_blacklisted_album(album_name):
        return None
    return DiscoveryResult(album=album_name, matched_title=matched_title, matched_artist=matched_artist)
