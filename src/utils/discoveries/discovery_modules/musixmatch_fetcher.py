import json
import re
from urllib.parse import quote

from utils.common.debug import flog
from utils.common.normalizer import fold_diacritics
from utils.common.request_pacing import RequestPacer
from utils.discoveries.discovery_result import LyricsDiscoveryResult
from utils.discoveries.lyrics_pages import fetch_page
from config.constants import MUSIXMATCH_LYRICS_URL, MUSIXMATCH_MIN_INTERVAL

# Musixmatch (musixmatch.com): a song's page, its address made from the names
# ("Agnieszka-Musiał/Wolnym-tempem") - the site search answers 403 - with the
# text in the page's __NEXT_DATA__. Plain text only. Knows old Polish songs
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


def get_lyrics(artist: str, title: str, duration_s: float | None = None, songs=None) -> list[LyricsDiscoveryResult]:
    """The song's page under its names as they are, else without diacritics
    (the site redirects a near spelling to its own)."""
    addresses = dict.fromkeys(f"{MUSIXMATCH_LYRICS_URL}/{quote(slug(a))}/{quote(slug(t))}"
                              for a, t in ((artist, title), (fold_diacritics(artist), fold_diacritics(title)))
                              if slug(a) and slug(t))
    for url in addresses:
        data = _page_data(url)
        if not data:
            continue
        track = data.get("track") or {}
        body = ((data.get("lyrics") or {}).get("body") or "").strip()
        instrumental = track.get("isInstrumental") is True
        if not (body or instrumental):
            flog(f"[Musixmatch] {url} has no lyrics")
            return []
        return [LyricsDiscoveryResult(plain=body or None, instrumental=instrumental and not body,
                                      matched_title=track.get("name"), matched_artist=track.get("artistName"),
                                      source_ref=str(track.get("id") or url))]
    return []
