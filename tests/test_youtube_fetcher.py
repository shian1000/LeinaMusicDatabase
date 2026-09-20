import importlib.util
import os
import sys
from pathlib import Path

from bs4 import BeautifulSoup

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))


def load_youtube_fetcher_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "utils"
        / "discoveries"
        / "discovery_modules"
        / "youtube_fetcher.py"
    )
    spec = importlib.util.spec_from_file_location("youtube_fetcher", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


SEARCH_RESULTS_HTML = """
<div>
  <ytmusic-responsive-list-item-renderer>
    <yt-formatted-string class="title" title="Bohemian Rhapsody">Bohemian Rhapsody</yt-formatted-string>
    <a href="channel/UC1dfeR4HaWDbWqFHLkxsg1d">Queen</a>
    <a href="browse/MPREb_nightattheopera">A Night At The Opera</a>
  </ytmusic-responsive-list-item-renderer>
  <ytmusic-responsive-list-item-renderer>
    <yt-formatted-string class="title" title="Bohemian Rhapsody - Live at Wembley Stadium">Bohemian Rhapsody - Live at Wembley Stadium</yt-formatted-string>
    <a href="channel/UC1dfeR4HaWDbWqFHLkxsg1d">Queen</a>
    <a href="browse/MPREb_livemagic">Live Magic</a>
  </ytmusic-responsive-list-item-renderer>
  <ytmusic-responsive-list-item-renderer>
    <yt-formatted-string class="title" title="NIGHTMARE">NIGHTMARE</yt-formatted-string>
    <a href="channel/UCcodyko">Cody Ko</a>
    <a href="channel/UCyoungnut">Young Nut</a>
    <a href="browse/MPREb_nightmare">NIGHTMARE</a>
  </ytmusic-responsive-list-item-renderer>
</div>
"""


def test_find_matching_song_picks_studio_version_over_live_version():
    module = load_youtube_fetcher_module()
    soup = BeautifulSoup(SEARCH_RESULTS_HTML, "html.parser")

    match = module._find_matching_song(soup, "Queen", "Bohemian Rhapsody")

    assert match == ("Bohemian Rhapsody", "Queen", "A Night At The Opera")


def test_find_matching_song_returns_none_when_nothing_resembles_the_query():
    module = load_youtube_fetcher_module()
    soup = BeautifulSoup(SEARCH_RESULTS_HTML, "html.parser")

    assert module._find_matching_song(soup, "Nirvana", "Smells Like Teen Spirit") is None


def test_find_matching_song_handles_multiple_artist_links_on_one_row():
    module = load_youtube_fetcher_module()
    soup = BeautifulSoup(SEARCH_RESULTS_HTML, "html.parser")

    match = module._find_matching_song(soup, "Cody Ko", "NIGHTMARE")

    assert match == ("NIGHTMARE", "Cody Ko", "NIGHTMARE")


def test_find_matching_song_returns_none_without_any_rows():
    module = load_youtube_fetcher_module()
    soup = BeautifulSoup("<div>nothing here</div>", "html.parser")

    assert module._find_matching_song(soup, "Queen", "Bohemian Rhapsody") is None


def test_find_matching_song_skips_row_without_album_link():
    module = load_youtube_fetcher_module()
    html = """
    <ytmusic-responsive-list-item-renderer>
      <yt-formatted-string class="title" title="Bohemian Rhapsody">Bohemian Rhapsody</yt-formatted-string>
      <a href="channel/UC1dfeR4HaWDbWqFHLkxsg1d">Queen</a>
    </ytmusic-responsive-list-item-renderer>
    """
    soup = BeautifulSoup(html, "html.parser")

    assert module._find_matching_song(soup, "Queen", "Bohemian Rhapsody") is None
