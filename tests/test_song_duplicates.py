"""Duplicate songs (utils/database/song_duplicates.py): which titles count as
one song, which row is kept, merging without losing anything, the silent
spelling-only pass of "Resolve duplicated artists" (remove_duplicate_songs())
and the "Resolve duplicated songs" review. In-memory SQLite with the
song-reference log."""

import importlib
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import song_references as sr
from utils.database.datatables import Artist, Base, Song, SongIdChange
from utils.database.song_artists import add_additional_artist, additional_artist_role, additional_artists
from utils.database.song_duplicates import (
    clean_title,
    credit_feat_artists,
    duplicate_key,
    feat_credits,
    find_duplicate_groups,
    looks_like_versions,
    merge_songs,
    missing_feat_artists,
    spelling_key,
)

rd = importlib.import_module("menu.main_menu.enter_database.manage_database.resolve_duplicates")


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    sr.register_song_reference_sync(factory)
    session = factory()
    yield session
    session.close()


class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


@pytest.mark.parametrize("a, b", [
    ("Savana (feat. Darko Rundek)", "Savana feat. Darko Rundek"),
    ("APT.", "APT feat. Bruno Mars"),
    ("Sorcererz", "Sorcererz (Visualiser)"),
    ("CZY TU JEST NASZ DOM", "CZY TU JEST NASZ DOM (TELEDYSK)"),
    ("Равнодушие", "Равнодушие ft. Сюзанна"),
    ("I Was Made For Lovin' You", "I Was Made For Lovin’ You (Official Video)"),
])
def test_same_song_keys(a, b):
    assert duplicate_key(a) == duplicate_key(b)


@pytest.mark.parametrize("a, b", [
    ("Again", "Again (Maceo Plex Remix)"),
    ("Spider and the Fly", "Spider and the Fly (acoustic)"),
    ("Baker Street", "Baker Street (Long Version)"),
    ("Pretty Fly", "Pretty Fly (For a White Guy)"),  # a subtitle stays: not proposed
])
def test_versions_are_songs_of_their_own(a, b):
    assert duplicate_key(a) != duplicate_key(b)


def test_groups_keep_the_fullest_row_and_flag_versions(session):
    rose, pluto, other = Artist(name="ROSÉ"), Artist(name="Unlike Pluto"), Artist(name="Other")
    apt = Song(title="APT.", artist=rose)
    apt_feat = Song(title="APT feat. Bruno Mars", artist=rose, youtube_video_id="v1", year=2024)
    cover = Song(title="No Scrubs ft. Joanna Jones (Cover)", artist=pluto)
    original = Song(title="No Scrubs", artist=pluto)
    session.add_all([apt, apt_feat, cover, original, Song(title="APT.", artist=other)])
    session.commit()

    groups = find_duplicate_groups(session)
    assert [[s.id for s in g] for g in groups] == [[apt_feat.id, apt.id], [cover.id, original.id]]
    assert not looks_like_versions(groups[0]) and looks_like_versions(groups[1])
    assert find_duplicate_groups(session, spelling_key) == []  # only spelling differs: none here


def test_merge_loses_nothing_and_logs_the_id(session):
    gorillaz, snoop = Artist(name="Gorillaz"), Artist(name="Snoop Dogg")
    kept = Song(title="Sorcererz (Visualiser)", artist=gorillaz, youtube_video_id="v1")
    dropped = Song(title="Sorcererz", artist=gorillaz, album="Cracker Island", year=2023, genre="pop",
                   spotify_url="https://open.spotify.com/x", youtube_video_id="v2")
    session.add_all([kept, dropped])
    session.flush()
    add_additional_artist(dropped, snoop, "feat")
    session.commit()
    dropped_id = dropped.id

    merge_songs(session, kept, dropped)
    session.commit()

    assert kept.title == "Sorcererz"  # junk-only bracket gone
    assert (kept.album, kept.year, kept.genre, kept.spotify_url) == ("Cracker Island", 2023, "pop",
                                                                      "https://open.spotify.com/x")
    assert kept.youtube_video_id == "v1"  # the kept song's own value wins
    assert [a.name for a in additional_artists(kept)] == ["Snoop Dogg"]
    assert session.get(Song, dropped_id) is None
    assert [(c.old_song_id, c.new_song_id) for c in session.query(SongIdChange)] == [(dropped_id, kept.id)]


def test_clean_title_keeps_feat_credits():
    assert clean_title("Hollywood feat. Snoop Dogg (Visualiser)") == "Hollywood feat. Snoop Dogg"
    assert clean_title("Song (Remix)") == "Song (Remix)"
    assert clean_title("(Official Video)") == "(Official Video)"  # never emptied


def test_resolve_duplicated_artists_folds_spelling_only_duplicates(session, monkeypatch):
    sassja = Artist(name="Sassja")
    a = Song(title="Savana (feat. Darko Rundek)", artist=sassja)
    b = Song(title="Savana feat. Darko Rundek", artist=sassja, youtube_video_id="v1")
    near = Song(title="Savana (Visualiser)", artist=sassja)  # a near duplicate: left for the review
    session.add_all([a, b, near])
    session.commit()
    monkeypatch.setattr(rd, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(rd, "apply_song_id_changes", lambda: None)

    rd.remove_duplicate_songs()

    assert sorted(s.title for s in session.query(Song)) == ["Savana (Visualiser)", "Savana feat. Darko Rundek"]


def test_review_merges_only_the_ticked_groups(session, monkeypatch, capsys):
    from utils.ui import song_duplicate_review as review

    tac, pluto = Artist(name="TACONAFIDE"), Artist(name="Unlike Pluto")
    session.add_all([Song(title="Tamagotchi", artist=tac), Song(title="Tamagotchi feat. Dawid Podsiadło", artist=tac),
                     Song(title="No Scrubs ft. Joanna Jones (Cover)", artist=pluto),
                     Song(title="No Scrubs", artist=pluto)])
    session.commit()
    monkeypatch.setattr(review, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(review, "apply_song_id_changes", lambda: None)
    offered = []

    def checkbox(message, choices):
        offered.extend((c.title, c.checked) for c in choices)
        return _Answer([c.value for c in choices if c.checked])

    monkeypatch.setattr(review.questionary, "checkbox", checkbox)
    from utils.database import database_sessions
    database_sessions.set_global_database_sessions((session, None))  # find_matching_artist() reads it
    try:
        review.resolve_duplicated_songs()
    finally:
        database_sessions.set_global_database_sessions(None)

    # The groups (the cover version unticked), then the featured artist to credit
    assert [checked for _, checked in offered] == [True, False, True]
    assert sorted(s.title for s in session.query(Song)) == ["No Scrubs", "No Scrubs ft. Joanna Jones (Cover)",
                                                            "Tamagotchi"]
    tamagotchi = session.query(Song).filter(Song.title == "Tamagotchi").one()
    assert [(a.name, additional_artist_role(tamagotchi, a)) for a in additional_artists(tamagotchi)] == [
        ("Dawid Podsiadło", "feat")]


def test_feat_credits_and_the_ones_the_kept_song_lacks(session, monkeypatch):
    from utils.database import database_sessions

    assert feat_credits("Hollywood feat. Snoop Dogg & Jamie Principle (Visualiser)") == ["Snoop Dogg", "Jamie Principle"]
    assert feat_credits("Savana (feat. Darko Rundek)") == ["Darko Rundek"]
    assert feat_credits("Tamagotchi") == []

    rose, bruno = Artist(name="ROSÉ"), Artist(name="Bruno Mars")
    apt, apt_feat = Song(title="APT.", artist=rose), Song(title="APT feat. Bruno Mars & Someone New", artist=rose)
    session.add_all([apt, apt_feat, bruno])
    session.flush()
    add_additional_artist(apt, bruno, "feat")  # already credited: not offered again
    session.commit()
    assert missing_feat_artists([apt, apt_feat]) == ["Someone New"]

    # The artist of that name is reused, an unknown one created
    database_sessions.set_global_database_sessions((session, None))
    try:
        credit_feat_artists(session, apt_feat, ["Bruno Mars", "Someone New"])
        session.commit()
    finally:
        database_sessions.set_global_database_sessions(None)
    assert [a.name for a in additional_artists(apt_feat)] == ["Bruno Mars", "Someone New"]
    assert session.query(Artist).filter(Artist.name == "Bruno Mars").count() == 1
