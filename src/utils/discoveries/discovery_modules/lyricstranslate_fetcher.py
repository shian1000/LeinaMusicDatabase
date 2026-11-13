import re
from urllib.parse import quote

from utils.common.debug import flog
from utils.common.normalizer import fold_diacritics
from utils.common.request_pacing import RequestPacer
from utils.discoveries.discovery_result import LyricsDiscoveryResult
from utils.discoveries.lyrics_pages import fetch_page
from config.constants import LYRICSTRANSLATE_LYRICS_URL, LYRICSTRANSLATE_MIN_INTERVAL

# LyricsTranslate (lyricstranslate.com): a song's page, its address made from
# the names without diacritics ("Afion-Okruglo-Zeljice-lyrics.html"). Plain
# text only; strong where the others are weak - Balkan, Slavic and other
# smaller languages. At the owner's choice (2026-11-12), for the private catalog.
MODULE_NAME = "LyricsTranslate (Curl)"

_pacer = RequestPacer(LYRICSTRANSLATE_MIN_INTERVAL)
# og:title "Afion - Okruglo zeljiče lyrics" / "CRAZY BLUES - Lot (Lot) (English translation)"
_PAGE_TITLE = re.compile(r"^(?P<artist>.+?) - (?P<title>.+?)(?: lyrics| \(English translation\))$")


def slug(name: str) -> str:
    """"Okruglo zeljiče" -> "Okruglo-Zeljice" (the site's own form: no
    diacritics, words capitalized)."""
    words = re.sub(r"[\W_]+", " ", fold_diacritics(name)).split()
    return "-".join(word[:1].upper() + word[1:] for word in words)


def page_lyrics(soup) -> str | None:
    """The stanzas of a lyrics page, one line per line, a blank line between
    them; of a translation's page, the original's."""
    stanzas = []
    for par in soup.select("#song-body div.par, #original-lyrics div.par"):
        lines = [line.get_text().strip() for line in par.find_all("div", recursive=False)] or [par.get_text().strip()]
        stanzas.append("\n".join(line for line in lines if line))
    return "\n\n".join(s for s in stanzas if s) or None


def get_lyrics(artist: str, title: str, duration_s: float | None = None, songs=None) -> list[LyricsDiscoveryResult]:
    if not (slug(artist) and slug(title)):
        return []
    # A song only translated has no page of its own; its English translation's
    # page holds the original too ("crazy-blues-lot-english")
    for url in (f"{LYRICSTRANSLATE_LYRICS_URL}/{quote(slug(artist))}-{quote(slug(title))}-lyrics.html",
                f"{LYRICSTRANSLATE_LYRICS_URL}/{quote(slug(artist).lower())}-{quote(slug(title).lower())}-english"):
        soup = fetch_page(url, "LyricsTranslate", _pacer)
        if soup is not None:
            break
    else:
        return []
    meta = soup.find("meta", property="og:title")
    names = _PAGE_TITLE.match((meta.get("content") or "").strip()) if meta else None
    text = page_lyrics(soup)
    if not (names and text):
        flog(f"[LyricsTranslate] no lyrics or names on {url}")
        return []
    return [LyricsDiscoveryResult(plain=text, matched_title=names["title"], matched_artist=names["artist"],
                                  source_ref=url)]
