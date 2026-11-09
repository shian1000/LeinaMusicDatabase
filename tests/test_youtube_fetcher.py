"""YouTube fetcher's "Songs" results, on answers shaped like music.youtube.com's
JSON search: no network."""
import pytest

from utils.discoveries.discovery_modules import youtube_fetcher as youtube


def _linked(text, page_type):
    return {"text": text, "navigationEndpoint": {"browseEndpoint": {"browseEndpointContextSupportedConfigs": {
        "browseEndpointContextMusicConfig": {"pageType": page_type}}}}}


def _column(runs):
    return {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": runs}}}


def _row(title, artists, album=None):
    details = [_linked(artist, "MUSIC_PAGE_TYPE_ARTIST") for artist in artists]
    if album:
        details += [{"text": " • "}, _linked(album, "MUSIC_PAGE_TYPE_ALBUM")]
    details.append({"text": " • 5:55"})
    return {"musicResponsiveListItemRenderer": {"flexColumns": [_column([{"text": title}]), _column(details)]}}


def _answer(*rows):
    return {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": [{"musicShelfRenderer": {"contents": list(rows)}}]}}}}]}}}


SEARCH_RESULTS = _answer(
    _row("Bohemian Rhapsody", ["Queen"], "A Night At The Opera"),
    _row("Bohemian Rhapsody - Live at Wembley Stadium", ["Queen"], "Live Magic"),
    _row("NIGHTMARE", ["Cody Ko", "Young Nut"], "NIGHTMARE"),
)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(youtube._pacer, "wait", lambda: None)


def test_find_matching_song_picks_studio_version_over_live_version():
    match = youtube._find_matching_song(SEARCH_RESULTS, "Queen", "Bohemian Rhapsody")

    assert match == ("Bohemian Rhapsody", "Queen", "A Night At The Opera")


def test_find_matching_song_returns_none_when_nothing_resembles_the_query():
    assert youtube._find_matching_song(SEARCH_RESULTS, "Nirvana", "Smells Like Teen Spirit") is None


def test_find_matching_song_handles_multiple_artist_links_on_one_row():
    match = youtube._find_matching_song(SEARCH_RESULTS, "Cody Ko", "NIGHTMARE")

    assert match == ("NIGHTMARE", "Cody Ko", "NIGHTMARE")


def test_find_matching_song_returns_none_without_any_rows():
    assert youtube._find_matching_song({"contents": {}}, "Queen", "Bohemian Rhapsody") is None


def test_find_matching_song_skips_row_without_album_link():
    data = _answer(_row("Bohemian Rhapsody", ["Queen"]))

    assert youtube._find_matching_song(data, "Queen", "Bohemian Rhapsody") is None


def test_find_matching_song_matches_collab_stored_as_one_joined_artist():
    # The DB stores a collab as one joined artist ("A x B x C") while YouTube
    # Music lists each artist separately; no single artist clears the
    # threshold on its own, so the row's artists must be joined back together.
    data = _answer(_row("Bahato", ["Sw@da", "MAXIM", "Niczos"], "Bahato"))

    match = youtube._find_matching_song(data, "Sw@da x Maxim x Niczos", "Bahato")

    assert match == ("Bahato", "Sw@da", "Bahato")
