from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from utils.selenium_sessions import get_global_driver

# -- Internal helpers ----------------------------------------------------------

# Maps Google's data-attrid suffix to friendly field names.
# Confirmed from real HTML:
#   data-attrid="kc:/music/recording_cluster:artist"
#   data-attrid="kc:/music/recording_cluster:first album"
#   data-attrid="kc:/music/recording_cluster:release date"
#   data-attrid="kc:/music/recording_cluster:skos_genre"
ATTRID_MAP = {
    "artist":       "artists",
    "first album":  "album",
    "release date": "released",
    "skos_genre":   "genre",
    "label":        "label",
    "duration":     "duration",
    "composer":     "composer",
}

def get_album_name(artist, title):
    print("WORK IN PROGRESS")
    return None




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


# -- Public API ----------------------------------------------------------------

def get_album_from_google(artist: str, song: str) -> dict | None:
    """
    Search Google for '<artist> - <song>' and extract the Knowledge Panel.
    Uses the global driver — call open_global_driver() before using this.

    Args:
        artist: Artist name, e.g. "Hamilton Leithauser"
        song:   Song title, e.g. "In a Black Out"

    Returns:
        Dict with available fields, e.g.:
            {
                "artists":  "Hamilton Leithauser, Rostam Batmanglij",
                "album":    "I Had a Dream That You Were Mine",
                "released": "2016",
                "genre":    "Alternative/Indie",
                "_query":   {"artist": ..., "song": ...}
            }
        Returns None if the driver is not open or no panel was found.
    """
    driver = get_global_driver()
    if driver is None:
        print("[Error] No driver open. Call open_global_driver() first.")
        return None

    query = f"{artist} - {song} song"
    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=en"

    driver.get(url)

    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "[data-attrid]"))
        )
    except Exception:
        print("[Result] Page loaded but no Knowledge Panel detected.")
        return None

    soup = BeautifulSoup(driver.page_source, "html.parser")

    info = {}
    for div in soup.find_all("div", attrs={"data-attrid": True}):
        attrid = div.get("data-attrid", "")
        if "music/recording_cluster" not in attrid:
            continue

        field_raw = attrid.split(":")[-1].lower()
        friendly_name = ATTRID_MAP.get(field_raw)

        if friendly_name and friendly_name not in info:
            value = _extract_value(div)
            if value:
                info[friendly_name] = value

    if not info:
        print("[Result] No Knowledge Panel found for this song.")
        return None

    info["_query"] = {"artist": artist, "song": song}
    return info


def pretty_print(info: dict) -> None:
    """Print scraped song info in a readable format."""
    if not info:
        print("No information found.")
        return

    q = info.pop("_query", {})
    print(f"\n{'='*45}")
    print(f"  {q.get('artist', '')} -- {q.get('song', '')}")
    print(f"{'='*45}")
    for key, value in info.items():
        print(f"  {key.capitalize():<12}: {value}")
    print()