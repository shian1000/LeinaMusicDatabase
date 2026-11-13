"""Lyrics web pages: what the page-reading lyrics modules share (fetch_page(),
text_lines()), and Song actions -> "Lyrics" -> "Read the lyrics from a web
page" (lyrics_from_page()): a page the owner found, read by its site's own
module when there is one, else by the block of the page holding the most
lines - a one-off, never a site the lookup asks by itself."""
import re
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from config.constants import HTTP_LOOKUP_TIMEOUT
from utils.common.debug import flog

HEADERS = {"User-Agent": "MusicDatabase (private music catalog)"}
# A block of fewer lines is no song's text (a page's menu, a credit line)
_MIN_LINES = 4
# A block beside it of at least this many lines is another stanza
_STANZA_MIN_LINES = 2
_BLOCKS = ("div", "p", "pre", "td", "span", "section", "article")


def fetch_page(url: str, site: str, pacer=None) -> BeautifulSoup | None:
    """The page, None when the site has no such page (404). Raises
    LookupError when it can't be had (no network, 403, a Cloudflare wall)."""
    if pacer is not None:
        pacer.wait()
    try:
        response = requests.get(url, headers=HEADERS, timeout=HTTP_LOOKUP_TIMEOUT)
    except requests.RequestException as exc:
        raise LookupError(f"{site} {url} failed: {exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise LookupError(f"{site} {url} answered {response.status_code}")
    # No charset in the headers: requests assumes Latin-1 (altwall.net is Windows-1251)
    if response.encoding and response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding
    return BeautifulSoup(response.text, "html.parser")


def text_lines(element) -> str:
    """An element's text, one line per line, at most one blank line between
    stanzas - none when every "stanza" is one line (a page spacing every
    line, teksty-pesenok.pro)."""
    plain = re.sub(r"\n{3,}", "\n\n", "\n".join(line.strip() for line in element.get_text().splitlines()).strip())
    stanzas = plain.split("\n\n")
    return "\n".join(stanzas) if len(stanzas) > 2 and all("\n" not in stanza for stanza in stanzas) else plain


def _line_count(element) -> int:
    return sum(1 for line in element.get_text().splitlines() if line.strip())


def _largest_block(soup) -> str | None:
    """The text of the block holding the most lines that holds no block of its
    own - with the blocks of its kind right beside it, a stanza each
    ("<p>" per stanza: spiewnikniepodleglosci.pl)."""
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for junk in soup.find_all(["script", "style", "noscript"]):
        junk.decompose()
    best, best_lines = None, _MIN_LINES - 1
    for block in soup.find_all(_BLOCKS):
        if not block.find(_BLOCKS) and (lines := _line_count(block)) > best_lines:
            best, best_lines = block, lines
    text = None
    if best is not None:
        siblings = best.parent.find_all(best.name, recursive=False) if best.parent else [best]
        start = end = siblings.index(best)
        while start > 0 and _line_count(siblings[start - 1]) >= _STANZA_MIN_LINES:
            start -= 1
        while end + 1 < len(siblings) and _line_count(siblings[end + 1]) >= _STANZA_MIN_LINES:
            end += 1
        text = "\n\n".join(text_lines(stanza) for stanza in siblings[start:end + 1])
    per_child = _line_per_child(soup)
    if per_child and (text is None or per_child.count("\n") > text.count("\n")):
        return per_child
    return text


def _line_per_child(soup) -> str | None:
    """A block whose blocks are a line each, an empty one between stanzas
    (poetryfoundation.org's poems)."""
    best, best_lines = None, _MIN_LINES - 1
    for block in soup.find_all(_BLOCKS):
        lines = block.find_all(_BLOCKS, recursive=False)
        if len(lines) < _MIN_LINES or any(line.find(_BLOCKS) or _line_count(line) > 1 for line in lines):
            continue
        if (count := sum(1 for line in lines if _line_count(line))) > best_lines:
            best, best_lines = lines, count
    if best is None:
        return None
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.get_text().strip() for line in best)).strip()


def _readers() -> dict:
    """{site: reader(soup) -> text or None} of the sites a lyrics module reads."""
    from utils.discoveries.discovery_modules import (cuspajz_fetcher, fajnanuta_fetcher, genius_lyrics_fetcher,
                                                     lyricstranslate_fetcher, musixmatch_fetcher, tekstowo_fetcher)
    return {
        "genius.com": lambda soup: genius_lyrics_fetcher.page_lyrics(str(soup)),
        "lyricstranslate.com": lyricstranslate_fetcher.page_lyrics,
        "tekstowo.pl": tekstowo_fetcher.page_lyrics,
        "fajnanuta.pl": fajnanuta_fetcher.page_lyrics,
        "cuspajz.com": cuspajz_fetcher.page_lyrics,
        "musixmatch.com": lambda soup: ((musixmatch_fetcher.track_data(soup) or {}).get("lyrics") or {}).get("body"),
        # Macedonian folk songs: the original under "lang-mk", translations beside it
        "pesna.org": lambda soup: _text_of(soup.select_one("div[id^='lang-'] .whitespace-pre-line")),
        "sonichits.com": lambda soup: _text_of(soup.select_one("#dLyrics")),
        # The original; its translation is the tab beside it ("music__translate")
        "teksty-pesenok.pro": lambda soup: _text_of(soup.select_one("#music__text")),
        "alternatifim.com": lambda soup: _text_of(soup.select_one(".sarkisozu")),
        "tekstovi.net": lambda soup: _text_of(soup.select_one("section.lyrics")),
        # Any Blogger blog (hrpjesme.blogspot.com): the post, not the sidebar's tag cloud
        "blogspot.com": lambda soup: _text_of(soup.select_one(".post-body")),
    }


def _reader_for(site: str):
    """The reader of `site` or of a domain it is under (lyrics.alternatifim.com)."""
    readers = _readers()
    parts = site.split(".")
    return next((readers[".".join(parts[i:])] for i in range(len(parts) - 1) if ".".join(parts[i:]) in readers), None)


def _text_of(element) -> str | None:
    """text_lines() of an element whose lines are "<br>"s - one already followed
    by a new line (Tekstowo's "<br/>\\n") adds none."""
    if element is None:
        return None
    for br in element.find_all("br"):
        following = br.next_sibling
        br.replace_with("" if isinstance(following, str) and following.startswith("\n") else "\n")
    return text_lines(element)


def lyrics_from_page(url: str) -> str | None:
    """The lyrics on the page at `url`, None when it holds none that can be
    told apart. Raises LookupError when the page can't be had."""
    site = (urlsplit(url).hostname or "").removeprefix("www.")
    soup = fetch_page(url, site or "page")
    if soup is None:
        return None
    reader = _reader_for(site)
    text = reader(soup) if reader else _largest_block(soup)
    if not text:
        flog(f"[Lyrics page] no lyrics told apart on {url}")
    return (text or "").strip() or None
