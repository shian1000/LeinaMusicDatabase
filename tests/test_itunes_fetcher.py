"""iTunes fetcher on fixed Search API answers: no network."""
from types import SimpleNamespace

import pytest
import requests

from utils.discoveries.discovery_modules import itunes_fetcher as itunes


def _track(title, album, artist="Doja Cat", kind="song"):
    return {"kind": kind, "trackName": title, "collectionName": album, "artistName": artist}


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(itunes._pacer, "wait", lambda: None)


def test_find_track_reads_album_title_and_artist():
    results = [_track("Cyber Sex", "Hot Pink"), _track("Say So", "Hot Pink")]

    assert itunes._find_track(results, "Doja Cat", "Say So") == ("Hot Pink", "Say So", "Doja Cat")


def test_find_track_skips_anything_but_songs():
    results = [_track("Say So", "Say So Video", kind="music-video"), _track("Say So", "Hot Pink")]

    assert itunes._find_track(results, "Doja Cat", "Say So")[0] == "Hot Pink"


def test_find_track_reports_singles_for_single_only_release():
    results = [_track("Say So (feat. Nicki Minaj)", "Say So (feat. Nicki Minaj) - Single")]

    assert itunes._find_track(results, "Doja Cat", "Say So") == ("Singles", "Say So", "Doja Cat")


def test_find_track_prefers_the_album_over_the_single():
    results = [_track("Say So", "Say So - Single"), _track("Say So", "Hot Pink")]

    assert itunes._find_track(results, "Doja Cat", "Say So")[0] == "Hot Pink"


def test_find_track_prefers_the_plain_title_over_a_live_version():
    results = [_track("Say So (Live in Paris)", "Tour"), _track("Say So", "Hot Pink")]

    assert itunes._find_track(results, "Doja Cat", "Say So")[0] == "Hot Pink"


def test_find_track_matches_one_of_several_credited_artists():
    results = [_track("Say So (Remix)", "Remixes - Single", artist="Doja Cat & Jax Jones")]

    assert itunes._find_track(results, "Jax Jones", "Say So") == ("Singles", "Say So", "Doja Cat & Jax Jones")


def test_find_track_returns_none_on_artist_mismatch():
    assert itunes._find_track([_track("Say So", "Hot Pink")], "Nirvana", "Say So") is None


def test_find_track_returns_none_on_title_mismatch():
    assert itunes._find_track([_track("Say So", "Hot Pink")], "Doja Cat", "Smells Like Teen Spirit") is None


def test_find_track_skips_a_blacklisted_album(monkeypatch):
    monkeypatch.setattr(itunes, "is_blacklisted_album", lambda album, artist=None: album == "Greatest Hits")
    results = [_track("Say So", "Greatest Hits"), _track("Say So", "Hot Pink")]

    assert itunes._find_track(results, "Doja Cat", "Say So")[0] == "Hot Pink"


def test_get_album_name_sends_the_whole_query_and_reports_the_match(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params)
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"results": [_track("Say So", "Hot Pink")]})
    monkeypatch.setattr(itunes.requests, "get", fake_get)

    result = itunes.get_album_name("Doja Cat", "Say So")

    assert (result.album, result.matched_title, result.matched_artist) == ("Hot Pink", "Say So", "Doja Cat")
    assert calls[0]["term"] == "Doja Cat Say So"
    assert calls[0]["entity"] == "song"


def test_get_album_name_logs_a_failed_request(monkeypatch):
    logged = []

    def failing_get(url, params, timeout):
        raise requests.ConnectionError("no network")
    monkeypatch.setattr(itunes.requests, "get", failing_get)
    monkeypatch.setattr(itunes, "flog", logged.append)

    assert itunes.get_album_name("Doja Cat", "Say So") is None
    assert logged and "no network" in logged[0]
