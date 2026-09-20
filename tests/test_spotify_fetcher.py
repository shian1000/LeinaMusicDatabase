import importlib.util
import os
import sys
from pathlib import Path

from bs4 import BeautifulSoup

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))


def load_spotify_fetcher_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "utils"
        / "discoveries"
        / "discovery_modules"
        / "spotify_fetcher.py"
    )
    spec = importlib.util.spec_from_file_location("spotify_fetcher", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


SEARCH_RESULTS_HTML = """
<div>
  <div data-testid="tracklist-row">
    <a href="/track/2JiDi0qAXsPwhPqA2qaKGt">Bohemian Rhapsody</a>
    <a href="/artist/1dfeR4HaWDbWqFHLkxsg1d">Queen</a>
  </div>
  <div data-testid="tracklist-row">
    <a href="/track/0LQmP5mwOOhOcaZoPaquNL">Bohemian Rhapsody - Live at Wembley Stadium</a>
    <a href="/artist/1dfeR4HaWDbWqFHLkxsg1d">Queen</a>
  </div>
</div>
"""

TRACK_PAGE_HTML = """
<section data-testid="track-page">
  <span data-testid="entityTitle"><h1>Bohemian Rhapsody</h1></span>
  <a data-testid="creator-link" href="/artist/1dfeR4HaWDbWqFHLkxsg1d">Queen</a>
  <a href="/album/1TkbyIkf6GSrO5e7gWS4AM">A Night At The Opera</a>
</section>
"""


def test_find_matching_track_picks_studio_version_over_live_version():
    module = load_spotify_fetcher_module()
    soup = BeautifulSoup(SEARCH_RESULTS_HTML, "html.parser")

    match = module._find_matching_track(soup, "Queen", "Bohemian Rhapsody")

    assert match == ("/track/2JiDi0qAXsPwhPqA2qaKGt", "Bohemian Rhapsody", "Queen")


def test_find_matching_track_returns_none_when_nothing_resembles_the_query():
    module = load_spotify_fetcher_module()
    soup = BeautifulSoup(SEARCH_RESULTS_HTML, "html.parser")

    assert module._find_matching_track(soup, "Nirvana", "Smells Like Teen Spirit") is None


def test_extract_album_from_track_page_reads_album_title_and_artist():
    module = load_spotify_fetcher_module()
    soup = BeautifulSoup(TRACK_PAGE_HTML, "html.parser")

    album, matched_title, matched_artist = module._extract_album_from_track_page(soup)

    assert album == "A Night At The Opera"
    assert matched_title == "Bohemian Rhapsody"
    assert matched_artist == "Queen"


def test_extract_album_from_track_page_returns_none_without_track_page_section():
    module = load_spotify_fetcher_module()
    soup = BeautifulSoup("<div>nothing here</div>", "html.parser")

    assert module._extract_album_from_track_page(soup) is None
