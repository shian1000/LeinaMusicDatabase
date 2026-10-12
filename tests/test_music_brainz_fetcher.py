import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from utils.discoveries.discovery_modules import music_brainz_fetcher
from utils.discoveries.discovery_modules.music_brainz_fetcher import _pick_release, get_album_name


def _release(title, primary_type, date=None, secondary_types=()):
    release = {"title": title, "release-group": {"primary-type": primary_type,
                                                 "secondary-type-list": list(secondary_types)}}
    if date:
        release["date"] = date
    return release


def _recording(title, releases):
    return {"title": title, "artist-credit": [{"artist": {"name": "The Rare Occasions"}}],
            "release-list": releases}


# Real MusicBrainz search result for "The Rare Occasions - Notion", trimmed:
# the compilations and the later singles come before the EP the song came out on.
NOTION_RECORDINGS = [
    _recording("Notion", [
        _release("Viral Rock Hits", "Album", "2023-03-24", ["Compilation"]),
        _release("Indie/Rock Playlist: December 2016", "Other", "2016-12-16", ["Compilation"]),
        _release("Notion (Cinematic)", "Single"),
        _release("Notion", "Single", "2021-12-02"),
        _release("Rock Summer", "Album", "2022-06-10", ["Compilation"]),
        _release("Futureproof", "EP", "2016-08-05"),
    ]),
    _recording("Notion (Acoustic)", [_release("Notion", "Single", "2021-12-02")]),
]


def test_ep_beats_earlier_listed_singles_when_there_is_no_album():
    title, recording = _pick_release(NOTION_RECORDINGS)
    assert title == "Futureproof"
    assert recording["title"] == "Notion"


def test_studio_album_still_wins_over_ep():
    recordings = [_recording("Song", [
        _release("Early Days", "EP", "2010-01-01"),
        _release("Into the Night", "Album", "2012-01-01"),
    ])]
    assert _pick_release(recordings)[0] == "Into the Night"


def test_earliest_single_wins_and_undated_goes_last():
    recordings = [_recording("Song", [
        _release("Song (Remastered)", "Single"),
        _release("Song (Live Version)", "Single", "2019"),
        _release("Song", "Single", "2015-05"),
    ])]
    assert _pick_release(recordings)[0] == "Song"


def test_later_recording_cannot_outrank_the_first_ones_releases():
    # An acoustic version's earlier EP shouldn't replace the main recording's single.
    recordings = [
        _recording("Song", [_release("Song", "Single", "2020-01-01")]),
        _recording("Song (Acoustic)", [_release("Acoustic Sessions", "EP", "2018-01-01")]),
    ]
    assert _pick_release(recordings)[0] == "Song"


def test_only_compilations_gives_nothing():
    recordings = [_recording("Song", [_release("Hits 2020", "Album", "2020", ["Compilation"])])]
    assert _pick_release(recordings) == (None, None)


def test_get_album_name_returns_the_ep(monkeypatch):
    monkeypatch.setattr(music_brainz_fetcher.musicbrainzngs, "search_recordings",
                        lambda query, limit: {"recording-list": NOTION_RECORDINGS})
    monkeypatch.setattr(music_brainz_fetcher, "check_spelling", lambda artist, song: {"found": False})
    monkeypatch.setattr(music_brainz_fetcher.time, "sleep", lambda seconds: None)

    result = get_album_name("The Rare Occasions", "Notion")

    assert result.album == "Futureproof"
    assert result.matched_title == "Notion"
