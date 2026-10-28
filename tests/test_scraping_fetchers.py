"""The scraping fetchers end to end on fixed HTML (fake_browser.FakeBrowser,
no Chrome, no network): Google's music Knowledge Panel (album, year, the
CAPTCHA page, no panel), Spotify's year from a track or album page and from
a stored link, YouTube's year from a stored or searched video and its album
from music.youtube.com's "Songs" results (DSC-04, YT-17)."""
from types import SimpleNamespace
from urllib.parse import quote, quote_plus

import pytest

from fake_browser import FakeBrowser
from utils.discoveries.discovery_modules import google_search_fetcher as google
from utils.discoveries.discovery_modules import spotify_fetcher as spotify
from utils.discoveries.discovery_modules import youtube_fetcher as youtube


# ==================== Google ====================

def _google_url(artist, query):
    return f"https://www.google.com/search?q={quote_plus(f'{artist} - {query}')}&hl=en"


# The recording_cluster panel as seen on a real song query (attrid names from the module's comment)
GOOGLE_SONG_PANEL = """
<div data-attrid="kc:/music/recording_cluster:artist">
  <span class="w8qArf">Artists: </span>
  <span class="LrzXr kno-fv"><a>Hamilton Leithauser</a>, <a>Rostam</a></span></div>
<div data-attrid="kc:/music/recording_cluster:first album">
  <span class="LrzXr kno-fv"><a>I Had a Dream That You Were Mine</a></span></div>
<div data-attrid="kc:/music/recording_cluster:release date">
  <span class="LrzXr kno-fv">September 23, 2016</span></div>
<div data-attrid="kc:/local:address"><span class="LrzXr">not music</span></div>
"""


@pytest.fixture
def no_snapshot(tmp_path, monkeypatch):
    """The no-panel branch saves the page as debug.html - into tmp, not the repo."""
    monkeypatch.setattr(google, "PROJECT_ROOT", tmp_path)
    return tmp_path


def test_google_album_and_artists_from_the_song_panel(monkeypatch, no_snapshot):
    FakeBrowser({_google_url("Hamilton Leithauser", "A 1000 Times"): GOOGLE_SONG_PANEL}).install(google, monkeypatch)

    result = google.get_album_name("Hamilton Leithauser", "A 1000 Times")
    assert result.album == "I Had a Dream That You Were Mine"
    assert result.matched_artist == "Hamilton Leithauser, Rostam"


def test_google_year_from_the_album_panel(monkeypatch, no_snapshot):
    panel = ('<div data-attrid="kc:/music/album:release date"><span class="LrzXr">2016</span></div>'
             '<div data-attrid="kc:/music/album:artist"><span class="LrzXr"><a>Rostam</a></span></div>')
    FakeBrowser({_google_url("Rostam", "Half-Light"): panel}).install(google, monkeypatch)

    result = google.get_release_year("Rostam", "Half-Light", is_single=False)
    assert (result.year, result.matched_artist) == (2016, "Rostam")


def test_google_without_a_music_panel_finds_nothing(monkeypatch, no_snapshot):
    page = '<div data-attrid="kc:/local:address"><span class="LrzXr">Main St 1</span></div>'
    FakeBrowser({_google_url("A", "B"): page}).install(google, monkeypatch)

    assert google.get_album_name("A", "B") is None
    assert google.get_release_year("A", "B", is_single=True) is None


def test_google_page_without_any_panel_is_saved_for_diagnosis(monkeypatch, no_snapshot):
    FakeBrowser({_google_url("A", "B"): "<html>no attrid at all</html>"}).install(google, monkeypatch)

    assert google.get_album_name("A", "B") is None
    assert "no attrid at all" in (no_snapshot / "debug.html").read_text()


def test_google_captcha_page_is_skipped(monkeypatch, no_snapshot):
    browser = FakeBrowser({}).install(google, monkeypatch)
    browser.get = lambda url: setattr(browser, "current_url", "https://www.google.com/sorry/index?continue=x")

    assert google.get_album_name("A", "B") is None
    assert not (no_snapshot / "debug.html").exists()


def test_google_panel_with_a_date_but_no_year_gives_no_year(monkeypatch, no_snapshot):
    panel = '<div data-attrid="kc:/music/recording_cluster:release date"><span class="LrzXr">soon</span></div>'
    FakeBrowser({_google_url("A", "B"): panel}).install(google, monkeypatch)

    assert google.get_release_year("A", "B", is_single=True) is None


# ==================== Spotify ====================

SPOTIFY_TRACKS = """
<div data-testid="tracklist-row"><a href="/track/T1">Bohemian Rhapsody</a><a href="/artist/Q">Queen</a></div>
"""
SPOTIFY_TRACK_PAGE = """
<section data-testid="track-page">
  <span data-testid="entityTitle"><h1>Bohemian Rhapsody</h1></span>
  <a data-testid="creator-link" href="/artist/Q">Queen</a>
  <span data-testid="release-date">October 31, 1975</span>
</section>
"""
SPOTIFY_ALBUMS = """
<div data-encore-id="card"><a href="/album/LIVE">x</a><p data-encore-id="cardTitle" title="Live Killers"></p>
  <a href="/artist/Q">Queen</a></div>
<div data-encore-id="card"><a href="/album/OPERA">x</a><p data-encore-id="cardTitle">A Night at the Opera</p>
  <a href="/artist/Q">Queen</a></div>
<div data-encore-id="card"><a href="/album/COVER">x</a><p data-encore-id="cardTitle">A Night at the Opera</p>
  <a href="/artist/X">Tribute Band</a></div>
"""
SPOTIFY_ALBUM_PAGE = """
<section data-testid="album-page">
  <span data-testid="entityTitle">A Night at the Opera</span>
  <a data-testid="creator-link" href="/artist/Q">Queen</a>
  <span data-testid="release-date">1975</span>
</section>
"""


def test_spotify_single_year_from_its_track_page(monkeypatch):
    FakeBrowser({
        spotify._search_url("Queen Bohemian Rhapsody", "tracks"): SPOTIFY_TRACKS,
        "https://open.spotify.com/track/T1": SPOTIFY_TRACK_PAGE,
    }).install(spotify, monkeypatch)

    result = spotify.get_release_year("Queen", "Bohemian Rhapsody", is_single=True)
    assert (result.year, result.matched_title, result.matched_artist) == (1975, "Bohemian Rhapsody", "Queen")


def test_spotify_album_year_picks_the_matching_card(monkeypatch):
    browser = FakeBrowser({
        spotify._search_url("Queen A Night at the Opera", "albums"): SPOTIFY_ALBUMS,
        "https://open.spotify.com/album/OPERA": SPOTIFY_ALBUM_PAGE,
    }).install(spotify, monkeypatch)

    result = spotify.get_release_year("Queen", "A Night at the Opera", is_single=False)
    assert result.year == 1975
    assert browser.visited[-1] == "https://open.spotify.com/album/OPERA"


def test_spotify_find_matching_album_needs_title_and_artist():
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(SPOTIFY_ALBUMS, "html.parser")
    assert spotify._find_matching_album(soup, "Queen", "A Night at the Opera") == "/album/OPERA"
    assert spotify._find_matching_album(soup, "Queen", "Jazz") is None
    assert spotify._find_matching_album(soup, "ABBA", "A Night at the Opera") is None


def test_spotify_no_results_page_is_saved_and_gives_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(spotify, "DEBUG_SNAPSHOT_FILE", tmp_path / "debug_spotify.html")
    FakeBrowser({}).install(spotify, monkeypatch)

    assert spotify.get_release_year("Queen", "Nothing", is_single=True) is None
    assert (tmp_path / "debug_spotify.html").exists()


def test_spotify_page_without_a_release_date_gives_no_year(monkeypatch):
    page = SPOTIFY_TRACK_PAGE.replace('data-testid="release-date"', 'data-testid="other"')
    FakeBrowser({"https://open.spotify.com/track/T1": page}).install(spotify, monkeypatch)

    assert spotify.get_release_year_from_url("https://open.spotify.com/track/T1") is None


@pytest.mark.parametrize("url, expected", [
    ("https://open.spotify.com/album/4Ab9?si=xyz", "https://open.spotify.com/album/4Ab9"),
    ("open.spotify.com/intl-pl/track/7Qq", "https://open.spotify.com/track/7Qq"),
    ("HTTPS://OPEN.SPOTIFY.COM/ALBUM/AbC", "https://open.spotify.com/album/AbC"),
    ("  https://open.spotify.com/track/X1  ", "https://open.spotify.com/track/X1"),
    ("https://open.spotify.com/playlist/37i9", None),
    ("https://example.com/album/1", None),
    ("", None),
    (None, None),
])
def test_normalize_spotify_url(url, expected):
    assert spotify.normalize_spotify_url(url) == expected


def test_spotify_year_straight_from_a_stored_album_link(monkeypatch):
    browser = FakeBrowser({"https://open.spotify.com/album/OPERA": SPOTIFY_ALBUM_PAGE}).install(spotify, monkeypatch)

    result = spotify.get_release_year_from_url("https://open.spotify.com/intl-de/album/OPERA?si=1")
    assert result.year == 1975
    assert browser.visited == ["https://open.spotify.com/album/OPERA"]  # no search
    assert spotify.get_release_year_from_url("https://open.spotify.com/playlist/1") is None


# ==================== YouTube ====================

def _song(video_id):
    return SimpleNamespace(youtube_video_id=video_id)


@pytest.fixture
def videos(monkeypatch):
    """get_video_release_year / search_video_ytdlp faked: years by video id."""
    years = {}
    searched = []
    monkeypatch.setattr(youtube, "get_video_release_year", lambda video_id: years.get(video_id))
    monkeypatch.setattr(youtube, "search_video_ytdlp", lambda artist, query: searched.append(query) or "found1")
    return SimpleNamespace(years=years, searched=searched)


def test_youtube_year_from_the_stored_video_skips_the_search(videos):
    videos.years.update({"stored1": 1999, "found1": 2020})

    result = youtube.get_release_year("Hey", "Teksański", True, songs=[_song("stored1")])
    assert (result.year, result.needs_review, result.source_url) == (1999, True, "https://youtu.be/stored1")
    assert videos.searched == []


def test_youtube_year_searches_when_the_stored_video_is_gone(videos):
    videos.years.update({"found1": 2020})

    result = youtube.get_release_year("Hey", "Teksański", True, songs=[_song("dead1")])
    assert (result.year, result.source_url) == (2020, "https://youtu.be/found1")
    assert videos.searched == ["Teksański"]


def test_youtube_single_marked_without_a_video_isnt_looked_up(videos):
    assert youtube.get_release_year("Hey", "Teksański", True, songs=[_song("N/A")]) is None
    assert videos.searched == []


def test_youtube_album_skips_a_track_marked_without_a_video(videos):
    videos.years.update({"stored2": 2001})

    result = youtube.get_release_year("Hey", "Karma", False, songs=[_song("N/A"), _song("stored2")])
    assert result.year == 2001


def test_youtube_no_year_anywhere_gives_nothing(videos):
    assert youtube.get_release_year("Hey", "Karma", False, songs=[]) is None


SEARCH_PAGE = '<ytmusic-chip-cloud-chip-renderer><yt-formatted-string>Songs</yt-formatted-string>' \
              '</ytmusic-chip-cloud-chip-renderer>'
SONG_RESULTS = """
<ytmusic-shelf-renderer>
  <ytmusic-responsive-list-item-renderer>
    <a class="title">Teksański</a><a href="channel/UC1">Hey</a><a href="browse/MPRE1">Ho!</a>
  </ytmusic-responsive-list-item-renderer>
</ytmusic-shelf-renderer>
"""


def _youtube_search(artist, title):
    return f"https://music.youtube.com/search?q={quote(f'{artist} {title}')}"


def test_youtube_album_from_the_songs_results(monkeypatch):
    url = _youtube_search("Hey", "Teksański")
    browser = FakeBrowser({url: SEARCH_PAGE}, on_click={url: SONG_RESULTS}).install(youtube, monkeypatch)

    result = youtube.get_album_name("Hey", "Teksański")
    assert (result.album, result.matched_title, result.matched_artist) == ("Ho!", "Teksański", "Hey")
    assert browser.clicks == 1  # the "Songs" filter


def test_youtube_without_the_songs_filter_finds_nothing(monkeypatch):
    FakeBrowser({_youtube_search("Hey", "X"): "<html>a channel page</html>"}).install(youtube, monkeypatch)
    assert youtube.get_album_name("Hey", "X") is None


def test_youtube_album_that_isnt_a_real_one_is_refused(monkeypatch):
    url = _youtube_search("Hey", "Teksański")
    blacklisted = SONG_RESULTS.replace("Ho!", "Greatest Hits")
    FakeBrowser({url: SEARCH_PAGE}, on_click={url: blacklisted}).install(youtube, monkeypatch)
    monkeypatch.setattr(youtube, "is_blacklisted_album", lambda album, artist=None: album == "Greatest Hits")

    assert youtube.get_album_name("Hey", "Teksański") is None


# ==================== Genius ====================

def test_genius_searches_the_whole_title_with_an_ampersand(monkeypatch):
    # DSC-26: spaces were the only thing escaped, so "&" or "#" ended the query
    from utils.discoveries.discovery_modules import genius_fetcher as genius
    browser = FakeBrowser({}).install(genius, monkeypatch)

    assert genius.get_album_name("Simon & Garfunkel", "Song #1") is None  # nothing on the fake page
    assert browser.visited[0] == "https://genius.com/search?q=Simon%20%26%20Garfunkel%20Song%20%231"


@pytest.mark.parametrize("a, b, same", [
    ("Hey", "Hey feat. Kayah", True),       # one artist credited with a guest
    ("Ka", "Kayah", False),                 # DSC-22: a short name inside a longer one
    ("Ra", "Rammstein", False),
    ("Kayah", "Kayah", True),
    ("", "Kayah", False),
])
def test_folder_youtube_pairing_compares_artists_as_whole_words(a, b, same):
    from utils.discoveries import folder_youtube_playlist as fyp
    assert fyp._artists_resemble(a, b) is same
