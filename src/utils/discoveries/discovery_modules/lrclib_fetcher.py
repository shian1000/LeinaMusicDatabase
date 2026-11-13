import time

import requests

from utils.common.debug import flog
from utils.common.request_pacing import RequestPacer
from utils.discoveries.discovery_result import LyricsDiscoveryResult
from config.constants import LRCLIB_API_URL, LRCLIB_MIN_INTERVAL, LRCLIB_RETRIES, LRCLIB_RETRY_WAIT_S, LRCLIB_TIMEOUT

# LRCLIB (lrclib.net): a community lyrics database, JSON, no key, mostly with
# time-synced lines. Leina Radio asks it too, when the server has no lyrics.
MODULE_NAME = "LRCLIB (Curl)"

# LRCLIB asks clients to name themselves
_HEADERS = {"User-Agent": "MusicDatabase (private music catalog)"}
_pacer = RequestPacer(LRCLIB_MIN_INTERVAL)


def _candidate(item: dict) -> LyricsDiscoveryResult | None:
    if not isinstance(item, dict):
        return None
    duration = item.get("duration")
    return LyricsDiscoveryResult(
        plain=item.get("plainLyrics") or None,
        synced=item.get("syncedLyrics") or None,
        instrumental=item.get("instrumental") is True,
        matched_title=item.get("trackName") or None,
        matched_artist=item.get("artistName") or None,
        duration_s=float(duration) if isinstance(duration, (int, float)) else None,
        source_ref=str(item["id"]) if item.get("id") is not None else None,
    )


def _search(artist: str, title: str):
    """LRCLIB's answer, waiting out a 429 (Retry-After, at most
    LRCLIB_RETRIES times). Raises when it can't be had - never [] for a
    failure, which would be remembered as "LRCLIB doesn't know the song"."""
    for attempt in range(LRCLIB_RETRIES + 1):
        _pacer.wait()
        response = requests.get(f"{LRCLIB_API_URL}/search", params={"track_name": title, "artist_name": artist},
                                headers=_HEADERS, timeout=LRCLIB_TIMEOUT)
        if response.status_code != 429 or attempt == LRCLIB_RETRIES:
            response.raise_for_status()
            return response.json()
        wait = response.headers.get("Retry-After", "")
        wait = int(wait) if wait.isdigit() else LRCLIB_RETRY_WAIT_S * (attempt + 1)
        flog(f"[LRCLIB] 429 for '{artist} - {title}', waiting {wait} s")
        print(f"LRCLIB asks to slow down, waiting {wait} s")
        time.sleep(wait)


def get_lyrics(artist: str, title: str, duration_s: float | None = None, songs=None) -> list[LyricsDiscoveryResult]:
    """Every song LRCLIB's search returns for the artist and title - the
    manager picks among them (title, artist, length); LRCLIB's own order
    isn't by fit. Raises when LRCLIB can't be asked."""
    try:
        items = _search(artist, title)
    except (requests.RequestException, ValueError) as exc:
        raise LookupError(f"LRCLIB search for '{artist} - {title}' failed: {exc}") from exc
    if not isinstance(items, list):
        raise LookupError(f"LRCLIB answered {type(items).__name__} for '{artist} - {title}'")
    return [c for c in map(_candidate, items) if c]
