"""Fetch database data -> "Check stored years" (utils/ui/year_check_review.py):
a stored year more than MUSICBRAINZ_REISSUE_AFTER_YEARS after MusicBrainz's
first release of the song offered (none ticked), the ticked ones changed, the
songs looked at marked year_checked so they aren't asked again; a song
MusicBrainz knows later or not at all left alone, one it couldn't be asked
about left unchecked, versions / projects / full albums not asked. MusicBrainz
and tag.db faked, in-memory music.db."""

import sys
from pathlib import Path

import pytest
import questionary
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config.constants import SONG_KIND_FULL_ALBUM
from utils.database.datatables import Artist, Base, Song
from utils.ui import year_check_review as review


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _songs(session):
    tresb, maanam = Artist(name="très.b"), Artist(name="Maanam")
    songs = {
        "reissue": Song(title="Ola", artist=tresb, year=2013, album="Męskie Granie"),
        "close": Song(title="Close", artist=tresb, year=2009),  # within the margin
        "earlier": Song(title="Cykady na Cykladach", artist=maanam, year=1981),  # MusicBrainz lacks the single
        "unknown": Song(title="Unknown", artist=maanam, year=2000),
        "failing": Song(title="Failing", artist=maanam, year=2000),
        "version": Song(title="Ola", artist=tresb, year=2015, version_kind="live"),
        "full": Song(title="Album", artist=tresb, year=2015, kind=SONG_KIND_FULL_ALBUM),
    }
    session.add_all(songs.values())
    session.flush()
    return songs


def _run(session, monkeypatch, songs, tick=lambda choices: [c.value for c in choices], checked=()):
    firsts = {"Ola": 2007, "Close": 2007, "Cykady na Cykladach": 1991, "Unknown": None}
    asked, tagged = [], []

    def first_appearance_year(artist, title, raise_errors=False):
        asked.append(title)
        if title == "Failing":
            raise ConnectionError("no network")
        return firsts[title]

    monkeypatch.setattr(review, "first_appearance_year", first_appearance_year)
    monkeypatch.setattr(review, "require_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(review, "submit_global_database_session", session.commit)
    monkeypatch.setattr(review, "load_max_songs_per_fetch", lambda: 50)
    monkeypatch.setattr(review, "song_ids_with_tag", lambda tag: set(checked))
    monkeypatch.setattr(review, "add_tag_to_songs", lambda ids, tag: tagged.extend(ids))
    shown = []

    def checkbox(_question, choices):
        shown.extend(choice.title for choice in choices)
        assert not any(choice.checked for choice in choices)
        picked = tick(choices)
        return type("Prompt", (), {"ask": lambda self: picked})()
    monkeypatch.setattr(questionary, "checkbox", checkbox)
    changed = review.check_stored_years()
    return changed, asked, set(tagged), shown


def test_a_reissues_year_is_offered_and_changed(session, monkeypatch):
    songs = _songs(session)
    changed, asked, tagged, shown = _run(session, monkeypatch, songs)

    assert changed == 1 and songs["reissue"].year == 2007
    assert shown == ["très.b - Ola [Męskie Granie]:  2013  ->  2007  (MusicBrainz's first release)"]
    assert (songs["close"].year, songs["earlier"].year, songs["unknown"].year) == (2009, 1981, 2000)
    assert sorted(asked) == ["Close", "Cykady na Cykladach", "Failing", "Ola", "Unknown"]  # no version, full album
    ids = {name: song.id for name, song in songs.items()}
    assert tagged == {ids["reissue"], ids["close"], ids["earlier"], ids["unknown"]}  # not the failed one


def test_cancelling_the_list_changes_nothing_and_asks_again(session, monkeypatch):
    songs = _songs(session)
    changed, _, tagged, _ = _run(session, monkeypatch, songs, tick=lambda choices: None)

    assert changed == 0 and songs["reissue"].year == 2013
    assert songs["reissue"].id not in tagged and songs["close"].id in tagged


def test_checked_songs_arent_asked_again(session, monkeypatch):
    songs = _songs(session)
    _, asked, _, _ = _run(session, monkeypatch, songs, checked=[songs["reissue"].id, songs["close"].id])

    assert sorted(asked) == ["Cykady na Cykladach", "Failing", "Unknown"]


def test_first_appearance_year_raises_on_a_failed_request_only_when_asked_to(monkeypatch):
    import musicbrainzngs
    from utils.discoveries.discovery_modules import music_brainz_fetcher as mb

    def search_recordings(**kw):
        raise musicbrainzngs.NetworkError("down")
    monkeypatch.setattr(mb.musicbrainzngs, "search_recordings", search_recordings)

    assert mb.first_appearance_year("Maanam", "Cykady na Cykladach") is None
    with pytest.raises(musicbrainzngs.WebServiceError):
        mb.first_appearance_year("Maanam", "Cykady na Cykladach", raise_errors=True)
