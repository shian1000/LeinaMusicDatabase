"""The API versions of the Spotify and Genius fetchers on fixed answers (mocked
requests, no network, no real keys)."""
from types import SimpleNamespace

import pytest
import requests

from utils.discoveries.discovery_modules import genius_api_fetcher as genius
from utils.discoveries.discovery_modules import spotify_api_fetcher as spotify


def _answer(data):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)


# ==================== Spotify ====================

def _track(name, album, album_type="album", release_date="1975-11-21", artist="Queen", album_artist="Queen"):
    return {"name": name, "artists": [{"name": artist}],
            "album": {"name": album, "album_type": album_type, "release_date": release_date,
                      "artists": [{"name": album_artist}]}}


@pytest.fixture
def spotify_api(monkeypatch):
    """Keys set, a fresh token state; `results` is what a search answers."""
    state = SimpleNamespace(results={}, token_requests=0, searches=[], warnings=[])

    def fake_post(url, data, headers, timeout):
        state.token_requests += 1
        return _answer({"access_token": "TOKEN", "expires_in": 3600})

    def fake_get(url, params, headers, timeout):
        state.searches.append((params, headers))
        return _answer(state.results)

    monkeypatch.setattr(spotify, "_token", None)
    monkeypatch.setattr(spotify, "_token_expires", 0.0)
    monkeypatch.setattr(spotify, "_warned_about_keys", False)
    monkeypatch.setattr(spotify._pacer, "wait", lambda: None)
    monkeypatch.setattr(spotify.settings, "spotify_client_id", "id")
    monkeypatch.setattr(spotify.settings, "spotify_client_secret", "secret")
    monkeypatch.setattr(spotify.requests, "post", fake_post)
    monkeypatch.setattr(spotify.requests, "get", fake_get)
    monkeypatch.setattr(spotify, "warn", state.warnings.append)
    return state


def test_spotify_album_of_the_matching_track(spotify_api):
    spotify_api.results = {"tracks": {"items": [_track("Other", "Z"), _track("Bohemian Rhapsody", "Opera")]}}

    result = spotify.get_album_name("Queen", "Bohemian Rhapsody")

    assert (result.album, result.matched_title, result.matched_artist) == ("Opera", "Bohemian Rhapsody", "Queen")
    params, headers = spotify_api.searches[0]
    assert params["q"] == "Queen Bohemian Rhapsody"
    assert params["type"] == "track"
    assert params["limit"] <= 10  # Spotify's cap for Development Mode apps
    assert headers["Authorization"] == "Bearer TOKEN"


def test_spotify_prefers_the_album_over_the_single_and_survives_null_items(spotify_api):
    spotify_api.results = {"tracks": {"items": [
        None, _track("Song", "Song", album_type="single"), _track("Song", "The Album"),
    ]}}

    assert spotify.get_album_name("Queen", "Song").album == "The Album"


def test_spotify_another_artist_is_refused(spotify_api):
    spotify_api.results = {"tracks": {"items": [_track("Song", "Album", artist="Nirvana")]}}

    assert spotify.get_album_name("Queen", "Song") is None


def test_spotify_blacklisted_album_is_refused(spotify_api, monkeypatch):
    spotify_api.results = {"tracks": {"items": [_track("Song", "Greatest Hits")]}}
    monkeypatch.setattr(spotify, "is_blacklisted_album", lambda album, artist=None: album == "Greatest Hits")

    assert spotify.get_album_name("Queen", "Song") is None


def test_spotify_various_artists_compilation_is_refused(spotify_api):
    spotify_api.results = {"tracks": {"items": [
        _track("Song", "Pop Gold", album_type="compilation", album_artist="Various Artists"),
    ]}}

    assert spotify.get_album_name("Queen", "Song") is None


def test_spotify_various_artists_soundtrack_stays(spotify_api):
    album = "Some Film (Original Motion Picture Soundtrack)"
    spotify_api.results = {"tracks": {"items": [_track("Song", album, album_type="compilation", album_artist="Various Artists")]}}

    assert spotify.get_album_name("Queen", "Song").album == album


def test_spotify_one_artists_compilation_stays(spotify_api):
    spotify_api.results = {"tracks": {"items": [_track("Song", "The Very Best", album_type="compilation")]}}

    assert spotify.get_album_name("Queen", "Song").album == "The Very Best"


def test_spotify_year_of_a_single_comes_from_its_album(spotify_api):
    spotify_api.results = {"tracks": {"items": [_track("Song", "Album", release_date="1984-02-01")]}}

    result = spotify.get_release_year("Queen", "Song", True)

    assert (result.year, result.matched_title, result.matched_artist) == (1984, "Song", "Queen")
    assert spotify_api.searches[0][0]["type"] == "track"


def test_spotify_year_of_an_album(spotify_api):
    spotify_api.results = {"albums": {"items": [
        {"name": "Jazz", "artists": [{"name": "Queen"}], "release_date": "1978"},
    ]}}

    assert spotify.get_release_year("Queen", "Jazz", False).year == 1978
    assert spotify_api.searches[0][0]["type"] == "album"


def test_spotify_without_a_usable_date_gives_no_year(spotify_api):
    spotify_api.results = {"tracks": {"items": [_track("Song", "Album", release_date="")]}}

    assert spotify.get_release_year("Queen", "Song", True) is None


def test_spotify_token_is_reused_until_it_nearly_expires(spotify_api, monkeypatch):
    spotify_api.results = {"tracks": {"items": []}}

    spotify.get_album_name("Queen", "A")
    spotify.get_album_name("Queen", "B")
    assert spotify_api.token_requests == 1

    monkeypatch.setattr(spotify, "_token_expires", 0.0)
    spotify.get_album_name("Queen", "C")
    assert spotify_api.token_requests == 2


def test_spotify_without_keys_warns_once_and_asks_nobody(spotify_api, monkeypatch):
    monkeypatch.setattr(spotify.settings, "spotify_client_secret", "")

    assert spotify.get_album_name("Queen", "A") is None
    assert spotify.get_release_year("Queen", "A", True) is None
    assert len(spotify_api.warnings) == 1
    assert spotify_api.token_requests == 0
    assert spotify_api.searches == []


@pytest.mark.parametrize("failing", ["post", "get"])
def test_spotify_failed_request_is_logged_without_the_keys(spotify_api, monkeypatch, failing):
    logged = []

    def fail(*args, **kwargs):
        raise requests.ConnectionError("no network")
    monkeypatch.setattr(spotify.requests, failing, fail)
    monkeypatch.setattr(spotify, "flog", logged.append)

    assert spotify.get_album_name("Queen", "A") is None
    assert any("no network" in line for line in logged)
    assert not any("secret" in line or "TOKEN" in line for line in logged)


# ==================== Genius ====================

def _hit(title="Say So", artist="Doja Cat", song_id=7, kind="song"):
    return {"type": kind, "result": {"id": song_id, "title": title, "primary_artist": {"name": artist}}}


@pytest.fixture
def genius_api(monkeypatch):
    """A token set; `hits` is what the search answers, `album` the song's record."""
    state = SimpleNamespace(hits=[], album={"name": "Hot Pink"}, calls=[], warnings=[])

    def fake_get(url, params, headers, timeout):
        state.calls.append((url, params, headers))
        if url == genius.GENIUS_API_SEARCH_URL:
            return _answer({"response": {"hits": state.hits}})
        return _answer({"response": {"song": {"album": state.album}}})

    monkeypatch.setattr(genius, "_warned_about_token", False)
    monkeypatch.setattr(genius._pacer, "wait", lambda: None)
    monkeypatch.setattr(genius.settings, "genius_access_token", "TOKEN")
    monkeypatch.setattr(genius.requests, "get", fake_get)
    monkeypatch.setattr(genius, "warn", state.warnings.append)
    return state


def test_genius_album_comes_from_the_matching_songs_record(genius_api):
    genius_api.hits = [_hit("Other", song_id=1), _hit("Say So", song_id=7)]

    result = genius.get_album_name("Doja Cat", "Say So")

    assert (result.album, result.matched_title, result.matched_artist) == ("Hot Pink", "Say So", "Doja Cat")
    search, song = genius_api.calls
    assert search[1] == {"q": "Doja Cat Say So"}
    assert song[0] == genius.GENIUS_API_SONG_URL.format(song_id=7)
    assert search[2]["Authorization"] == "Bearer TOKEN"


@pytest.mark.parametrize("hit", [
    _hit(artist="Nirvana"),            # same title, another artist
    _hit(title="Something Else"),
    _hit(kind="album"),
])
def test_genius_hit_that_doesnt_fit_is_not_opened(genius_api, hit):
    genius_api.hits = [hit]

    assert genius.get_album_name("Doja Cat", "Say So") is None
    assert len(genius_api.calls) == 1  # the search only


def test_genius_song_without_an_album_gives_nothing(genius_api):
    genius_api.hits = [_hit()]
    genius_api.album = None

    assert genius.get_album_name("Doja Cat", "Say So") is None


def test_genius_blacklisted_album_is_refused(genius_api, monkeypatch):
    genius_api.hits = [_hit()]
    monkeypatch.setattr(genius, "is_blacklisted_album", lambda album, artist=None: True)

    assert genius.get_album_name("Doja Cat", "Say So") is None


def test_genius_without_a_token_warns_once_and_asks_nobody(genius_api, monkeypatch):
    monkeypatch.setattr(genius.settings, "genius_access_token", "")

    assert genius.get_album_name("Doja Cat", "A") is None
    assert genius.get_album_name("Doja Cat", "B") is None
    assert len(genius_api.warnings) == 1
    assert genius_api.calls == []


def test_genius_failed_request_is_logged_without_the_token(genius_api, monkeypatch):
    logged = []

    def fail(*args, **kwargs):
        raise requests.ConnectionError("no network")
    monkeypatch.setattr(genius.requests, "get", fail)
    monkeypatch.setattr(genius, "flog", logged.append)

    assert genius.get_album_name("Doja Cat", "Say So") is None
    assert logged and "no network" in logged[0]
    assert not any("TOKEN" in line for line in logged)


def test_genius_has_no_year_lookup():
    # Its album pages include unreleased ones: docs/agent-notes/discovery-modules.md
    assert not hasattr(genius, "get_release_year")
