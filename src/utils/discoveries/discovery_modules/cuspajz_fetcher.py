import re
from urllib.parse import quote

from utils.common.debug import flog
from utils.common.request_pacing import RequestPacer
from utils.discoveries.discovery_modules.tekstowo_fetcher import slug as tekstowo_slug
from utils.discoveries.discovery_result import LyricsDiscoveryResult
from utils.discoveries.lyrics_pages import fetch_page, text_lines
from config.constants import CUSPAJZ_LYRICS_URL, CUSPAJZ_MIN_INTERVAL

# Cuspajz (cuspajz.com): a song's page, its address made from the names
# ("cuspajz.com/tekstovi-pjesama/pjesma/cinkusi/raca.html"); a page it doesn't
# have redirects to its index. Plain text only; the songs of the former
# Yugoslavia the others lack. Its terms don't allow reading its pages either;
# at the owner's choice (2026-11-13), for the private catalog.
MODULE_NAME = "Cuspajz (Curl)"

_pacer = RequestPacer(CUSPAJZ_MIN_INTERVAL)
# <title>: "Cinkuši — Raca — Tekst"
_PAGE_TITLE = re.compile(r"^(?P<artist>.+?) — (?P<title>.+?) — Tekst")


def slug(name: str) -> str:
    return tekstowo_slug(name.replace("đ", "d").replace("Đ", "D"))


def page_lyrics(soup) -> str | None:
    """The page's stanzas, a "<p class=text>" each."""
    return "\n\n".join(text_lines(stanza) for stanza in soup.select("article p.text")) or None


def get_lyrics(artist: str, title: str, duration_s: float | None = None, songs=None) -> list[LyricsDiscoveryResult]:
    if not (slug(artist) and slug(title)):
        return []
    url = f"{CUSPAJZ_LYRICS_URL}/{quote(slug(artist))}/{quote(slug(title))}.html"
    soup = fetch_page(url, "Cuspajz", _pacer)
    names = _PAGE_TITLE.match(soup.title.get_text(strip=True)) if soup is not None and soup.title else None
    plain = page_lyrics(soup) if names else None
    if not (names and plain):
        flog(f"[Cuspajz] no lyrics on {url}")  # the index a missing page redirects to has neither
        return []
    return [LyricsDiscoveryResult(plain=plain, matched_artist=names["artist"], matched_title=names["title"],
                                  source_ref=url)]
