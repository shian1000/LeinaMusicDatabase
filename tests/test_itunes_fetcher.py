import importlib.util
import os
import sys
from pathlib import Path

from bs4 import BeautifulSoup

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))


def load_itunes_fetcher_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "utils"
        / "discoveries"
        / "discovery_modules"
        / "itunes_fetcher.py"
    )
    spec = importlib.util.spec_from_file_location("itunes_fetcher", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


SEARCH_RESULTS_HTML = """
<div>
  <div data-testid="top-search-list-result">
    <li data-testid="top-search-list-result-title">
      <a data-testid="click-action" href="/album/say-so/1486465096?i=1486465101">Say So</a>
    </li>
    <li data-testid="top-search-list-result-subtitle">Song &middot; Doja Cat</li>
  </div>
  <div data-testid="top-search-list-result">
    <li data-testid="top-search-list-result-title">
      <a data-testid="click-action" href="/album/say-so-feat-nicki-minaj-single/1510821672?i=1510821685">Say So (feat. Nicki Minaj) - Single</a>
    </li>
    <li data-testid="top-search-list-result-subtitle">Album &middot; Doja Cat</li>
  </div>
</div>
"""

TRACK_LOCKUP_SEARCH_RESULTS_HTML = """
<div>
  <div data-testid="track-lockup">
    <span data-testid="track-lockup-title">Say So</span>
    <span data-testid="track-lockup-subtitle">Doja Cat</span>
    <a data-testid="click-action" href="/album/say-so/1486465096?i=1486465101"></a>
  </div>
  <div data-testid="track-lockup">
    <span data-testid="track-lockup-title">Say So (feat. Nicki Minaj)</span>
    <span data-testid="track-lockup-subtitle">Doja Cat</span>
    <a data-testid="click-action" href="/album/say-so-feat-nicki-minaj-single/1510821672?i=1510821685"></a>
  </div>
</div>
"""

ALBUM_PAGE_HTML = """
<div>
  <div data-testid="non-editable-product-title">Hot Pink</div>
  <div data-testid="product-subtitles">
    <a href="/artist/doja-cat/830588310">Doja Cat</a>
  </div>
  <div data-testid="track-list-item"><span data-testid="track-title">Cyber Sex</span></div>
  <div data-testid="track-list-item"><span data-testid="track-title">Say So</span></div>
</div>
"""

SINGLE_PAGE_HTML = """
<div>
  <div data-testid="non-editable-product-title">Say So (feat. Nicki Minaj) - Single</div>
  <div data-testid="product-subtitles">
    <a href="/artist/doja-cat/830588310">Doja Cat</a>
  </div>
  <div data-testid="track-list-item"><span data-testid="track-title">Say So (feat. Nicki Minaj)</span></div>
</div>
"""

MULTI_ARTIST_SINGLE_HTML = """
<div>
  <div data-testid="non-editable-product-title">Say So (Jax Jones Midnight Snack Remix) - Single</div>
  <div data-testid="product-subtitles">
    <a href="/artist/doja-cat/830588310">Doja Cat</a>
    <a href="/artist/jax-jones/1201228278">Jax Jones</a>
  </div>
  <div data-testid="track-list-item"><span data-testid="track-title">Say So (Jax Jones Midnight Snack Remix)</span></div>
</div>
"""


def test_find_song_link_matches_song_row_and_skips_album_rows():
    module = load_itunes_fetcher_module()
    soup = BeautifulSoup(SEARCH_RESULTS_HTML, "html.parser")

    link = module._find_song_link(soup, "Say So")

    assert link == "/album/say-so/1486465096?i=1486465101"


def test_find_song_link_returns_none_when_nothing_resembles_the_query():
    module = load_itunes_fetcher_module()
    soup = BeautifulSoup(SEARCH_RESULTS_HTML, "html.parser")

    assert module._find_song_link(soup, "Smells Like Teen Spirit") is None


def test_find_song_link_matches_track_lockup_layout():
    module = load_itunes_fetcher_module()
    soup = BeautifulSoup(TRACK_LOCKUP_SEARCH_RESULTS_HTML, "html.parser")

    link = module._find_song_link(soup, "Say So")

    assert link == "/album/say-so/1486465096?i=1486465101"


def test_extract_from_itunes_soup_reads_album_title_and_artist():
    module = load_itunes_fetcher_module()
    soup = BeautifulSoup(ALBUM_PAGE_HTML, "html.parser")

    album, matched_title, matched_artist = module.extract_from_itunes_soup(soup, "Doja Cat", "Say So")

    assert album == "Hot Pink"
    assert matched_title == "Say So"
    assert matched_artist == "Doja Cat"


def test_extract_from_itunes_soup_reports_singles_for_single_only_release():
    module = load_itunes_fetcher_module()
    soup = BeautifulSoup(SINGLE_PAGE_HTML, "html.parser")

    album, matched_title, matched_artist = module.extract_from_itunes_soup(soup, "Doja Cat", "Say So")

    assert album == "Singles"
    assert matched_title == "Say So"
    assert matched_artist == "Doja Cat"


def test_extract_from_itunes_soup_matches_against_first_credited_artist():
    module = load_itunes_fetcher_module()
    soup = BeautifulSoup(MULTI_ARTIST_SINGLE_HTML, "html.parser")

    album, matched_title, matched_artist = module.extract_from_itunes_soup(soup, "Jax Jones", "Say So")

    assert album == "Singles"
    assert matched_artist == "Doja Cat"


def test_extract_from_itunes_soup_returns_none_on_artist_mismatch():
    module = load_itunes_fetcher_module()
    soup = BeautifulSoup(ALBUM_PAGE_HTML, "html.parser")

    assert module.extract_from_itunes_soup(soup, "Nirvana", "Say So") is None


def test_extract_from_itunes_soup_returns_none_on_title_mismatch():
    module = load_itunes_fetcher_module()
    soup = BeautifulSoup(ALBUM_PAGE_HTML, "html.parser")

    assert module.extract_from_itunes_soup(soup, "Doja Cat", "Smells Like Teen Spirit") is None


def test_extract_from_itunes_soup_returns_none_without_product_title():
    module = load_itunes_fetcher_module()
    soup = BeautifulSoup("<div>nothing here</div>", "html.parser")

    assert module.extract_from_itunes_soup(soup, "Doja Cat", "Say So") is None
