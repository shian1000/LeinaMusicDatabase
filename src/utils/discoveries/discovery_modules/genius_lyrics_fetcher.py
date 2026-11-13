import re

import pycountry
import requests
from bs4 import BeautifulSoup

from utils.common.debug import flog
from utils.common.request_pacing import RequestPacer
from utils.common.script_utils import has_non_latin
from utils.common.text_utils import title_resembles
from utils.discoveries.discovery_result import LyricsDiscoveryResult
from config.constants import (
    GENIUS_LYRICS_PAGES_MAX,
    GENIUS_SITE_MIN_INTERVAL,
    GENIUS_SITE_SEARCH_URL,
    HTTP_LOOKUP_TIMEOUT,
)

# Genius (genius.com): its own site search (JSON, no key, no browser) and the
# lyrics page of a hit. Plain text only - no synced lines, no length - so a
# hit is only as sure as its title. Knows many songs LRCLIB doesn't, and says
# which songs are instrumental. Scraping its pages isn't what Genius's terms
# allow; it's here at the owner's choice (2026-11-12), for the private catalog.
MODULE_NAME = "Genius lyrics (Curl)"

_HEADERS = {"User-Agent": "MusicDatabase (private music catalog)"}
_pacer = RequestPacer(GENIUS_SITE_MIN_INTERVAL)
# Genius adds the Latin spelling to a title in another alphabet: "Гасі (Hasi)"
_LATIN_SUFFIX = re.compile(r"^(?P<title>.+?)\s*\((?P<latin>[^()]+)\)$")
# Section labels the page puts between the lines: "[Verse 1]", "[Chorus: X]"
_SECTION_LINE = re.compile(r"^\s*\[[^\]]*\]\s*$")


def _get(url: str, **params) -> requests.Response:
    _pacer.wait()
    response = requests.get(url, params=params or None, headers=_HEADERS, timeout=HTTP_LOOKUP_TIMEOUT)
    response.raise_for_status()
    return response


def _own_title(title: str) -> str:
    match = _LATIN_SUFFIX.match(title)
    if match and has_non_latin(match["title"]) and not has_non_latin(match["latin"]):
        return match["title"]
    return title


def page_lyrics(html: str) -> str | None:
    """The plain text of a Genius lyrics page: its lyrics containers without
    the header inside them and without the section labels."""
    soup = BeautifulSoup(html, "html.parser")
    parts = []
    for container in soup.select('div[data-lyrics-container="true"]'):
        for excluded in container.select('[data-exclude-from-selection="true"]'):
            excluded.decompose()
        for br in container.find_all("br"):
            br.replace_with("\n")
        parts.append(container.get_text())
    lines = [line.rstrip() for line in "\n".join(parts).splitlines() if not _SECTION_LINE.match(line)]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text or None


def country_codes(origin: str | None) -> list[str]:
    """"Poland - Red Ruthenia - Świlcza" / "#Japan" -> ["POL", "PL"] / ["JPN", "JP"]:
    how Genius tells apart artists of one name, with either code ("Arab (POL)",
    "Dorina (HU)"); [] when unknown."""
    country = (origin or "").split(" - ")[0].strip(" #")
    try:
        found = pycountry.countries.lookup(country) if country else None
    except LookupError:
        return []
    return [found.alpha_3, found.alpha_2] if found else []


def _search(query: str) -> list[dict]:
    try:
        sections = _get(GENIUS_SITE_SEARCH_URL, q=query, per_page=5).json()["response"]["sections"]
        return [hit["result"] for section in sections for hit in section.get("hits", []) if hit.get("type") == "song"]
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        raise LookupError(f"Genius search for '{query}' failed: {exc}") from exc


def _resembling(hits: list[dict], title: str) -> list[dict]:
    return [hit for hit in hits if hit.get("url") and (hit.get("primary_artist") or {}).get("name")
            and title_resembles(title, _own_title(hit.get("title") or ""))]


def get_lyrics(artist: str, title: str, duration_s: float | None = None, songs=None) -> list[LyricsDiscoveryResult]:
    """The song hits of a Genius search; the lyrics page is opened only for
    the first GENIUS_LYRICS_PAGES_MAX hits whose title resembles the song (the
    manager checks them again, with the artist). An instrumental hit needs no
    page. With no such hit, the search again with the artist's country as
    Genius writes it when two artists share a name ("Arab (POL) Moon": "Arab
    Moon" finds only "Arab Money"). Raises when Genius can't be asked."""
    hits = _resembling(_search(f"{artist} {title}"), title)
    for code in country_codes(getattr(getattr(songs[0], "artist", None), "origin", None)) if songs else []:
        if hits:
            break
        hits = _resembling(_search(f"{artist} ({code}) {title}"), title)
    found, opened = [], 0
    for hit in hits:
        matched_title = _own_title(hit.get("title") or "")
        matched_artist = hit["primary_artist"]["name"]
        url = hit["url"]
        result = LyricsDiscoveryResult(matched_title=matched_title, matched_artist=matched_artist,
                                       source_ref=str(hit.get("id") or url))
        if hit.get("instrumental") is True:
            result.instrumental = True
        elif opened < GENIUS_LYRICS_PAGES_MAX:
            opened += 1
            try:
                result.plain = page_lyrics(_get(url).text)
            except requests.RequestException as exc:
                raise LookupError(f"Genius page {url} failed: {exc}") from exc
            if result.plain is None:
                flog(f"[Genius lyrics] no lyrics on {url}")
                continue
        else:
            continue
        found.append(result)
    return found
