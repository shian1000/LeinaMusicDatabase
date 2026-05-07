import wikipedia
import requests
import re
import time
from bs4 import BeautifulSoup

from utils.debug import slog
from utils.text_utils import is_blacklisted_album
from utils.file_management import save_string_to_file


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FROM_ALBUM_MARKER = "from the album"
STRIP_CHARS = " \"''\u201c\u2018\u201d\u2019"
GENERIC_HEADINGS = {
    "track listing", "tracklist", "track list",
    "disc 1", "disc 2", "disc 3",
    "side a", "side b", "side c", "side d",
    "standard edition", "deluxe edition", "bonus tracks",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_album_name(artist: str, title: str) -> str | None:
    """Search Wikipedia for a song and return the album name, or None."""
    page = _fetch_wikipedia_page(artist, title)
    if not page:
        return None

    soup = _fetch_soup(page.url)

    if title.lower() in page.title.lower():
        album = _extract_from_infobox(soup, title)
    else:
        album = _extract_from_track_listing(soup, page.title, title)

    if not album:
        return None

    album = clean_album_title(album, artist)
    return None if is_blacklisted_album(album) else album


# ---------------------------------------------------------------------------
# Helpers: fetching
# ---------------------------------------------------------------------------

def _fetch_wikipedia_page(artist: str, title: str):
    """Search Wikipedia and return the first matching page, or None."""
    results = _search_with_retry(f"{artist} {title}")
    if not results:
        return None

    slog(results)

    try:
        page = wikipedia.page(results[0], auto_suggest=False)
    except (wikipedia.DisambiguationError, wikipedia.PageError):
        return None

    slog(f"Page title: {page.title}")
    return page


def _search_with_retry(query: str, retries: int = 3, delay: int = 2) -> list:
    """Run wikipedia.search with retries on JSONDecodeError."""
    for attempt in range(retries):
        try:
            return wikipedia.search(query)
        except requests.exceptions.JSONDecodeError:
            if attempt < retries - 1:
                time.sleep(delay)
    return []


def _fetch_soup(url: str) -> BeautifulSoup:
    headers = {"User-Agent": "SongAlbumFinder/1.0 (your-email@example.com)"}
    response = requests.get(url, headers=headers)
    slog(response)
    return BeautifulSoup(response.text, "html.parser")


# ---------------------------------------------------------------------------
# Helpers: extraction
# ---------------------------------------------------------------------------

def _extract_from_infobox(soup: BeautifulSoup, title: str) -> str | None:
    """Case 1: page is the song — pull album from the infobox 'from the album' field."""
    infobox = soup.find("table", class_=re.compile(r"\binfobox\b"))
    if not infobox:
        return None

    infobox_text = infobox.get_text(separator=" ", strip=True)
    slog(f"Infobox preview: {infobox_text[:300]}")

    idx = infobox_text.lower().find(FROM_ALBUM_MARKER)
    if idx == -1:
        return None

    after = (
        infobox_text[idx + len(FROM_ALBUM_MARKER):]
        .strip(STRIP_CHARS)
        .replace("\xa0", " ")
    )

    match = re.match(r"([^\"\n\(\)]+?)(?:\s{2,}|[BRG][-\s])", after) or \
            re.match(r"([^\",\.\n\(\)]+)", after)

    if not match:
        return None

    slog(soup)
    save_string_to_file(str(soup))

    album = match.group(1).strip(STRIP_CHARS)
    slog(f"Infobox album: {album}")
    return album or None


def _extract_from_track_listing(
    soup: BeautifulSoup, page_title: str, title: str
) -> str | None:
    """Case 2: page is the artist/album — scan track listing sections for the song."""
    for heading_text, anchor in _iter_heading_anchors(soup):
        slog(f"Checking heading: {heading_text}")

        if is_blacklisted_album(heading_text) and heading_text != "Singles":
            slog("Skipping blacklisted heading")
            continue

        album = _find_song_under_heading(anchor, heading_text, page_title, title)
        if album:
            return album

    slog("No track listing container matched")
    return None


def _find_song_under_heading(
    anchor, heading_text: str, page_title: str, title: str
) -> str | None:
    """Walk siblings under a heading and return the album name if the song is found."""
    for sibling in anchor.find_next_siblings():
        # Stop at the next heading or mw-heading wrapper
        if sibling.name and re.match(r"^h[1-4]$", sibling.name):
            break
        if sibling.name == "div" and "mw-heading" in " ".join(sibling.get("class", [])):
            break

        container = _find_track_container(sibling)
        if container is None:
            continue

        container_text = container.get_text(separator=" ", strip=True)
        if title.lower() not in container_text.lower():
            slog(f"Container found but song not in it: {container_text[:200]}")
            continue

        slog("Song found in container")
        if heading_text.lower() in GENERIC_HEADINGS:
            album = page_title
            slog(f"Generic heading — using page title: {album}")
        else:
            album = heading_text
            slog(f"Specific heading — using heading: {album}")
        return album

    return None


def _find_track_container(sibling):
    """Return the best track container element inside (or as) a sibling, or None."""
    # Explicit tracklist table
    if sibling.name == "table" and "tracklist" in sibling.get("class", []):
        slog("Case A: top-level tracklist table")
        return sibling

    nested = sibling.find("table", class_="tracklist")
    if nested:
        slog("Case A (nested): nested tracklist table")
        return nested

    # Plain list
    if sibling.name in ("ol", "ul"):
        slog("Case B: plain ol/ul list")
        return sibling

    nested_list = sibling.find(["ol", "ul"])
    if nested_list:
        slog("Case C: nested list inside sibling")
        return nested_list

    # Generic wikitable (e.g. Singles table)
    if sibling.name == "table":
        slog("Case D: plain wikitable")
        return sibling

    return None


def _iter_heading_anchors(soup: BeautifulSoup):
    """Yield (heading_text, anchor_element) for every h1–h4 in the page."""
    for node in soup.find_all(re.compile(r"^h[1-4]$")):
        parent = node.parent
        anchor = (
            parent
            if parent and "mw-heading" in " ".join(parent.get("class", []))
            else node
        )
        yield node.get_text(strip=True), anchor


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def clean_album_title(album_name: str, artist: str) -> str:
    # Try artist-specific pattern first
    pattern = re.compile(
        r"\s*\(\s*" + re.escape(artist) + r"\s+album\s*\)", re.IGNORECASE
    )
    cleaned = pattern.sub("", album_name).strip()
    if cleaned != album_name:
        return cleaned

    # Fallback: strip any " (... album)" suffix
    return re.sub(r"\s*\([^)]*\balbum\b[^)]*\)", "", album_name, flags=re.IGNORECASE).strip()