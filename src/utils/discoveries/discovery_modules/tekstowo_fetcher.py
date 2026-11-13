import re
from urllib.parse import quote

from utils.common.debug import flog
from utils.common.normalizer import fold_diacritics
from utils.common.request_pacing import RequestPacer
from utils.discoveries.discovery_result import LyricsDiscoveryResult
from utils.discoveries.lyrics_pages import fetch_page, text_lines
from config.constants import TEKSTOWO_MIN_INTERVAL, TEKSTOWO_URL

# Tekstowo.pl: a song's page, its address made from the names
# ("tekstowo.pl/alina-gingertail/w-moim-ogrodecku"). Plain text only; the
# largest Polish lyrics site. Its terms don't allow reading its pages either;
# at the owner's choice (2026-11-12), for the private catalog.
MODULE_NAME = "Tekstowo (Curl)"

_pacer = RequestPacer(TEKSTOWO_MIN_INTERVAL)


# How the site spells Cyrillic in its addresses: "Крутится Земля" -> "krutitsa-zemla"
_CYRILLIC = str.maketrans({**dict(zip("абвгдезийклмнопрстуфхцыэюяёіїєўґ", "abvgdezijklmnoprstufhcyeuaeiieug")),
                           "ж": "z", "ч": "c", "ш": "s", "щ": "s", "ъ": "", "ь": ""})


def slug(name: str) -> str:
    """"W moim ogródecku" -> "w-moim-ogrodecku" (no diacritics, lowercase)."""
    return re.sub(r"[\W_]+", "-", fold_diacritics(name).replace("ł", "l").replace("Ł", "L")).strip("-").lower()


def latin_slug(name: str) -> str:
    """slug() of a Cyrillic name as the site writes it in Latin letters."""
    return slug(name.lower().translate(_CYRILLIC))


def page_lyrics(soup) -> str | None:
    text = soup.select_one("#songText .inner-text")
    return text_lines(text) if text else None


def get_lyrics(artist: str, title: str, duration_s: float | None = None, songs=None) -> list[LyricsDiscoveryResult]:
    if not (slug(artist) and slug(title)):
        return []
    for url in dict.fromkeys(f"{TEKSTOWO_URL}/{quote(to_slug(artist))}/{quote(to_slug(title))}"
                             for to_slug in (slug, latin_slug)):
        soup = fetch_page(url, "Tekstowo", _pacer)
        if soup is not None:
            break
    else:
        return []
    heading = soup.find("h1")
    names = heading.get_text(" ", strip=True).split(" - ", 1) if heading else []
    plain = page_lyrics(soup)
    if len(names) != 2 or not plain:
        flog(f"[Tekstowo] no lyrics or names on {url}")
        return []
    return [LyricsDiscoveryResult(plain=plain, matched_artist=names[0], matched_title=names[1], source_ref=url)]
