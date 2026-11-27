from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from config.constants import PROJECT_ROOT
from utils.common.selenium_sessions import get_global_driver
from utils.common.debug import flog, slog
from utils.discoveries.discovery_result import DiscoveryResult, GenreDiscoveryResult, YearDiscoveryResult
import re
import time

# -- Internal helpers ----------------------------------------------------------

# Maps Google's data-attrid suffix to friendly field names.
# Confirmed from real HTML:
#   data-attrid="kc:/music/recording_cluster:artist"
#   data-attrid="kc:/music/recording_cluster:first album"
#   data-attrid="kc:/music/recording_cluster:release date"
#   data-attrid="kc:/music/recording_cluster:skos_genre"
MODULE_NAME = "Google search fetcher (Selenium)"
# The panel's genres say neither which recording they belong to nor where they
# come from: the owner reviews them (discoveries_manager.discover_genre())
GENRE_NEVER_SURE = True

ATTRID_MAP = {
    "artist":       "artists",
    "first album":  "album",
    "release date": "released",
    "skos_genre":   "genre",
    "label":        "label",
    "duration":     "duration",
    "composer":     "composer",
}


def _extract_value(el) -> str:
    """
    Extract the display value from a Knowledge Panel row element.

    Structure inside each data-attrid div:
        <span class="w8qArf">Artists: </span>
        <span class="LrzXr kno-fv ...">Hamilton Leithauser, Rostam Batmanglij</span>

    The value span always has class "LrzXr".
    Linked values (artists, album, genre) have text inside <a> tags.
    """
    value_span = el.find("span", class_="LrzXr")
    if value_span:
        links = value_span.find_all("a")
        if links:
            return ", ".join(a.get_text(strip=True) for a in links)
        return value_span.get_text(strip=True)
    return ""


# Set by _fetch_knowledge_panel_info(): Google couldn't be asked (no browser,
# CAPTCHA), which get_genre() raises for, so the song is asked again next time
blocked = False


# -- Public API ----------------------------------------------------------------

def _fetch_knowledge_panel_info(artist: str, query: str) -> dict | None:
    """Load a Google search for "artist - query" and parse its music
    Knowledge Panel (if any) into a {friendly_name: value} dict. Shared by
    get_album_name() (query = song title) and get_release_year() (query =
    either a song title or an album title).

    Accepts both the "music/recording_cluster" panel (seen for a plain
    song query) and "music/album" (assumed shape for an album-name query,
    based on Google's recording_cluster attrid naming convention - not yet
    confirmed against a live album panel, so get_release_year() may need
    its attrid handling revisited if this doesn't pan out)."""
    global blocked
    blocked = False
    driver = get_global_driver()
    if driver is None:
        flog("[Google] No browser available.")
        blocked = True
        return None

    url = f"https://www.google.com/search?q={quote_plus(f'{artist} - {query}')}&hl=en"

    driver.get(url)

    time.sleep(4)

    if "/sorry/" in driver.current_url:
        flog("[Google] Google flagged this request as automated traffic (CAPTCHA block) - skipping.")
        blocked = True
        return None

    try:
        WebDriverWait(driver, 8).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "[data-attrid]"))
        )
    except Exception:
        with open(PROJECT_ROOT / "debug.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        flog("[Google] Page loaded but no Knowledge Panel detected.")
        return None

    soup = BeautifulSoup(driver.page_source, "html.parser")

    info = {}
    for div in soup.find_all("div", attrs={"data-attrid": True}):
        attrid = div.get("data-attrid", "")
        if "music/recording_cluster" not in attrid and "music/album" not in attrid:
            continue

        field_raw = attrid.split(":")[-1].lower()
        friendly_name = ATTRID_MAP.get(field_raw)

        if friendly_name and friendly_name not in info:
            value = _extract_value(div)
            if value:
                info[friendly_name] = value

    if not info:
        flog(f"[Google] No music Knowledge Panel for '{artist} - {query}'")
        return None

    info["_query"] = {"artist": artist, "query": query}
    slog(info)
    return info


def get_album_name(artist: str, song: str) -> DiscoveryResult | None:
    info = _fetch_knowledge_panel_info(artist, song)
    if not info:
        return None

    album = info.get("album")
    if not album:
        return None
    # The panel's "first album" may be a compilation, and it says neither who
    # the album is by nor which recording it matched
    return DiscoveryResult(album=album, matched_artist=info.get("artists"), needs_review=True)


def get_release_year(artist: str, query: str, is_single: bool) -> YearDiscoveryResult | None:
    info = _fetch_knowledge_panel_info(artist, query)
    if not info:
        return None

    released = info.get("released")
    if not released:
        return None

    match = re.search(r"(\d{4})", released)
    if not match:
        return None
    return YearDiscoveryResult(year=int(match.group(1)), matched_artist=info.get("artists"))


def get_genre(artist: str, title: str, songs=None) -> GenreDiscoveryResult | None:
    info = _fetch_knowledge_panel_info(artist, title)
    if blocked:
        raise ConnectionError("Google couldn't be asked (no browser, or a CAPTCHA)")
    if not info or not info.get("genre"):
        return None
    # Linked values come joined by ", "; one link may hold two ("Alternative/Indie")
    names = [name.strip() for name in re.split(r"[,/]", info["genre"]) if name.strip()]
    return GenreDiscoveryResult(names, matched_artist=info.get("artists"), never_sure=True)
