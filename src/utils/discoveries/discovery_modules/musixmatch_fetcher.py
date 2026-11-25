import json
import re
from urllib.parse import quote

from utils.common.debug import flog
from utils.common.normalizer import fold_diacritics
from utils.common.request_pacing import RequestPacer
from utils.common.language_names import language_code
from utils.discoveries.discovery_result import LanguageDiscoveryResult, LyricsDiscoveryResult
from utils.discoveries.lyrics_pages import fetch_page
from config.constants import LANGUAGE_INSTRUMENTAL, MUSIXMATCH_LYRICS_URL, MUSIXMATCH_MIN_INTERVAL

# Musixmatch (musixmatch.com): a song's page, its address made from the names
# ("Agnieszka-Musiał/Wolnym-tempem") - the site search answers 403 - with the
# text and its language in the page's __NEXT_DATA__. Plain text only. Knows old Polish songs
# LRCLIB and Genius don't. Its terms don't allow reading its pages either; at
# the owner's choice (2026-11-12), for the private catalog.
MODULE_NAME = "Musixmatch (Curl)"

_pacer = RequestPacer(MUSIXMATCH_MIN_INTERVAL)


def slug(name: str) -> str:
    """"Help, I'm Alive" -> "Help-I-m-Alive"; letters of any alphabet stay."""
    return re.sub(r"[\W_]+", "-", name).strip("-")


def track_data(soup) -> dict | None:
    """The track and lyrics in a song page's __NEXT_DATA__."""
    script = soup.find("script", id="__NEXT_DATA__")
    try:
        return json.loads(script.string)["props"]["pageProps"]["data"]["trackInfo"]["data"]
    except (AttributeError, TypeError, KeyError, ValueError):
        return None


def _page_data(url: str) -> dict | None:
    """The page's track and lyrics, None for no such page. Raises when the site can't be asked."""
    soup = fetch_page(url, "Musixmatch", _pacer)
    data = track_data(soup) if soup is not None else None
    if soup is not None and data is None:
        flog(f"[Musixmatch] no track data on {url}")
    return data


def _track(artist: str, title: str) -> tuple[str, dict] | None:
    """(address, track and lyrics) of the song's page under its names as they
    are, else without diacritics (the site redirects a near spelling to its own)."""
    addresses = dict.fromkeys(f"{MUSIXMATCH_LYRICS_URL}/{quote(slug(a))}/{quote(slug(t))}"
                              for a, t in ((artist, title), (fold_diacritics(artist), fold_diacritics(title)))
                              if slug(a) and slug(t))
    for url in addresses:
        data = _page_data(url)
        if data:
            return url, data
    return None


def get_lyrics(artist: str, title: str, duration_s: float | None = None, songs=None) -> list[LyricsDiscoveryResult]:
    found = _track(artist, title)
    if found is None:
        return []
    url, data = found
    track = data.get("track") or {}
    body = ((data.get("lyrics") or {}).get("body") or "").strip()
    instrumental = track.get("isInstrumental") is True
    if not (body or instrumental):
        flog(f"[Musixmatch] {url} has no lyrics")
        return []
    return [LyricsDiscoveryResult(plain=body or None, instrumental=instrumental and not body,
                                  matched_title=track.get("name"), matched_artist=track.get("artistName"),
                                  source_ref=str(track.get("id") or url))]


def get_language(artist: str, title: str, songs=None) -> LanguageDiscoveryResult | None:
    """The language of the page's lyrics (what Shazam shows - its lyrics are
    Musixmatch's), or LANGUAGE_INSTRUMENTAL; None without lyrics or a language."""
    found = _track(artist, title)
    if found is None:
        return None
    url, data = found
    track = data.get("track") or {}
    code = ((data.get("lyrics") or {}).get("language") or "").lower()
    language = LANGUAGE_INSTRUMENTAL if track.get("isInstrumental") is True else language_code(code) if code else None
    if language is None:
        flog(f"[Musixmatch] {url} names no language")
        return None
    return LanguageDiscoveryResult(language, matched_title=track.get("name"), matched_artist=track.get("artistName"),
                                   source_ref=str(track.get("id") or url))
