import re
from urllib.parse import quote

from utils.common.debug import flog
from utils.common.request_pacing import RequestPacer
from utils.discoveries.discovery_modules.tekstowo_fetcher import slug
from utils.discoveries.discovery_result import LyricsDiscoveryResult
from utils.discoveries.lyrics_pages import fetch_page, text_lines
from config.constants import FAJNANUTA_LYRICS_URL, FAJNANUTA_MIN_INTERVAL

# Fajnanuta.pl: a song's page, its address made from the names
# ("anna-maria-jopek-branford-marsalis-patrz-i-sluchaj.html", the artists of a
# duet both in it). Plain text only; Polish songs Tekstowo lacks. Its terms
# don't allow reading its pages either; at the owner's choice (2026-11-12).
MODULE_NAME = "Fajnanuta (Curl)"

_pacer = RequestPacer(FAJNANUTA_MIN_INTERVAL)
# <title>: "▷ Anna Maria Jopek, Branford Marsalis - Patrz i Słuchaj TEKST - na Fajnanuta.pl"
_PAGE_TITLE = re.compile(r"^\W*(?P<artist>.+?) - (?P<title>.+?) TEKST\b")


def page_lyrics(soup) -> str | None:
    """The lyrics run through the "arttxt" blocks (an advert between them), under
    their "<title> TEKST PIOSENKI" heading and up to the first paragraph or box -
    the page's own blurb ("<title> w wykonaniu ..."), all a page without lyrics has."""
    parts = []
    for block in soup.select("div.arttxt"):
        for child in block.children:
            name = getattr(child, "name", None)
            if name in ("p", "div"):
                return text_lines("".join(parts)) or None
            if name == "br":
                parts.append("\n")
            elif name not in ("h2", "h3", "span"):
                parts.append(child.get_text() if name else str(child).strip())
    return text_lines("".join(parts)) or None


def get_lyrics(artist: str, title: str, duration_s: float | None = None, songs=None) -> list[LyricsDiscoveryResult]:
    if not (slug(artist) and slug(title)):
        return []
    url = f"{FAJNANUTA_LYRICS_URL}/{quote(slug(artist))}-{quote(slug(title))}.html"
    soup = fetch_page(url, "Fajnanuta", _pacer)
    if soup is None:
        return []
    names = _PAGE_TITLE.match(soup.title.get_text(strip=True)) if soup.title else None
    plain = page_lyrics(soup)
    if not (names and plain):
        flog(f"[Fajnanuta] no lyrics or names on {url}")
        return []
    return [LyricsDiscoveryResult(plain=plain, matched_artist=names["artist"], matched_title=names["title"],
                                  source_ref=url)]
