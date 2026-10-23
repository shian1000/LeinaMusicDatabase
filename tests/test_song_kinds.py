"""songs.kind (utils/database/song_kinds.py) and the "Find full albums" candidates
(utils/database/full_album_candidates.py): which titles say "whole album", the
title without those words, which songs an imported entry may match (a track
never a full album), marking a song as a full album files it under its own
title, the owner's ignore list, and the candidate signals. songs.kind is read
by Leina Radio, so these pin the contract. Temp-file SQLite."""

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from config.constants import FULL_ALBUM_MIN_DURATION_S, SINGLES_ALBUM, SONG_KIND_FULL_ALBUM
from utils.database import full_album_candidates as fac
from utils.database import song_kinds as sk
from utils.database.datatables import Artist, Base, Song


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture
def ignore_file(tmp_path, monkeypatch):
    path = tmp_path / "data" / "full_album_ignore.json"
    monkeypatch.setattr(sk, "_IGNORE_PATH", path)
    return path


def _song(title, kind=None, album=None, **kw):
    return Song(title=title, kind=kind, album=album, **kw)


# ==================== titles ====================

@pytest.mark.parametrize("title", [
    "Migration (Full Album)",
    "Migration [FULL ALBUM]",
    "Legenda - cały album",
    "Legenda - CALY ALBUM",  # diacritics ignored
    "Кино - Весь альбом",
    "Álbum Completo",
    "Rumours (Full AlbumEp)",  # glued to the next word
    "Hybrid Theory full length album",
])
def test_title_with_a_marker_says_full_album(title):
    assert sk.title_has_full_album_marker(title)


@pytest.mark.parametrize("title", [None, "", "Migration", "Full Moon", "Albumen", "Playful album", "Fullalbum"])
def test_title_without_a_marker_is_not_a_full_album(title):
    assert not sk.title_has_full_album_marker(title)


@pytest.mark.parametrize("title, expected", [
    ("Migration (Full Album)", "Migration"),
    ("Migration (Full Album).", "Migration"),
    ("Migration [Full Album Stream]", "Migration"),  # the longest marker goes whole
    ("Legenda (1991) - cały album", "Legenda (1991)"),
    ("Hybrid Theory | Full LP", "Hybrid Theory"),
    ("Hybrid Theory - full album 2000 remaster", "Hybrid Theory"),  # all after the marker
    ("Migration", "Migration"),
    ("(Full Album)", "(Full Album)"),  # nothing left: the title itself
])
def test_strip_full_album_marker(title, expected):
    assert sk.strip_full_album_marker(title) == expected


def test_looks_like_full_album_by_title_or_length():
    assert sk.looks_like_full_album("Migration (Full Album)")
    assert sk.looks_like_full_album("Migration", FULL_ALBUM_MIN_DURATION_S)
    assert not sk.looks_like_full_album("Migration", FULL_ALBUM_MIN_DURATION_S - 1)
    assert not sk.looks_like_full_album("Migration", None)


def test_kind_label_and_is_full_album():
    album, song = _song("Migration", kind=SONG_KIND_FULL_ALBUM), _song("Migration")

    assert sk.is_full_album(album) and not sk.is_full_album(song)
    assert (sk.kind_label(album), sk.kind_label(song)) == ("full album", "song")


# ==================== matching an entry ====================

def test_a_track_never_matches_a_full_album():
    """A track "Migration" must not be taken for the album "Migration"."""
    album = _song("Migration", kind=SONG_KIND_FULL_ALBUM)
    track = _song("Ashes")

    assert sk.songs_for_entry([album, track], "Migration") == [track]
    assert sk.songs_for_entry([album, track], "Migration", FULL_ALBUM_MIN_DURATION_S - 1) == [track]


def test_an_entry_that_looks_like_an_album_tries_full_albums_first():
    track = _song("Ashes")
    album = _song("Migration", kind=SONG_KIND_FULL_ALBUM)
    unmarked = _song("Bloom (Full Album)")  # its own title says so

    assert sk.songs_for_entry([track, album, unmarked], "Migration (Full Album)") == [album, unmarked, track]
    # a long file may still be a mix kept as a song: the songs follow
    assert sk.songs_for_entry([track, album], "Migration", FULL_ALBUM_MIN_DURATION_S) == [album, track]


# ==================== marking ====================

@pytest.mark.parametrize("album", [None, "", SINGLES_ALBUM])
def test_marking_a_full_album_files_it_under_its_own_title(album):
    song = _song("Migration (Full Album)", album=album)

    filed = sk.set_song_kind([song], SONG_KIND_FULL_ALBUM)

    assert song.kind == SONG_KIND_FULL_ALBUM
    assert song.album == "Migration"
    assert filed == [song]


def test_marking_keeps_a_known_album():
    song = _song("Migration (Full Album)", album="Migration (Deluxe)")

    assert sk.set_song_kind([song], SONG_KIND_FULL_ALBUM) == []
    assert (song.kind, song.album) == (SONG_KIND_FULL_ALBUM, "Migration (Deluxe)")


def test_unmarking_leaves_the_album():
    song = _song("Migration", kind=SONG_KIND_FULL_ALBUM, album="Migration")

    assert sk.set_song_kind([song], None) == []
    assert (song.kind, song.album) == (None, "Migration")


def test_an_unknown_kind_is_refused_before_any_change():
    song = _song("Migration")

    with pytest.raises(ValueError):
        sk.set_song_kind([song], "live_set")
    assert song.kind is None


def test_full_album_filter(session):
    artist = Artist(name="Bonobo")
    session.add_all([_song("Migration", kind=SONG_KIND_FULL_ALBUM, artist=artist), _song("Kerala", artist=artist)])
    session.commit()

    assert [s.title for s in session.query(Song).filter(sk.full_album_filter())] == ["Migration"]


# ==================== ignore list ====================

def test_ignore_list_starts_empty_and_adds_up(ignore_file):
    assert sk.load_full_album_ignore_list() == set()

    sk.add_to_full_album_ignore_list([5, 3])
    sk.add_to_full_album_ignore_list([3, 9])

    assert sk.load_full_album_ignore_list() == {3, 5, 9}
    assert json.loads(ignore_file.read_text()) == [3, 5, 9]


def test_an_unreadable_ignore_list_counts_as_empty(ignore_file):
    ignore_file.parent.mkdir(parents=True)
    ignore_file.write_text("{broken")

    assert sk.load_full_album_ignore_list() == set()


# ==================== candidates ====================

def test_candidates_and_their_reasons(session, ignore_file):
    long = FULL_ALBUM_MIN_DURATION_S + 5 * 60
    bonobo, aurora = Artist(name="Bonobo"), Artist(name="AURORA")
    by_title = _song("Migration (Full Album)", artist=bonobo)
    by_file = _song("Live at Brixton", artist=aurora)
    by_video = _song("A Different Kind of Human", artist=aurora, youtube_video_id="vid1")
    short_video = _song("Runaway", artist=aurora, youtube_video_id="vid2")
    marked = _song("Black Sands (Full Album)", artist=bonobo, kind=SONG_KIND_FULL_ALBUM)
    ignored = _song("Fragments (Full Album)", artist=bonobo)
    session.add_all([by_title, by_file, by_video, short_video, marked, ignored])
    session.commit()
    sk.add_to_full_album_ignore_list([ignored.id])

    candidates = fac.find_full_album_candidates(
        session, {by_file.id: long}, {"vid1": long, "vid2": FULL_ALBUM_MIN_DURATION_S - 1})

    # the title says so first, then by artist and title
    assert [c.song for c in candidates] == [by_title, by_video, by_file]
    first, video, file = candidates
    assert first.title_says_so and first.reasons == [fac.REASON_TITLE]
    assert first.cleaned_title == "Migration"
    assert video.reasons == ["YouTube 25 min"] and video.video_duration_s == long
    assert file.reasons == ["file 25 min"] and file.file_duration_s == long
    assert not file.title_says_so and file.cleaned_title == "Live at Brixton"


def test_candidates_without_youtube(session, ignore_file):
    session.add(_song("Kerala", artist=Artist(name="Bonobo"), youtube_video_id="vid1"))
    session.commit()

    assert fac.find_full_album_candidates(session, {}, None) == []


def test_unmarked_video_ids(session):
    artist = Artist(name="Bonobo")
    session.add_all([_song("Kerala", artist=artist, youtube_video_id="v1"),
                     _song("Cirrus", artist=artist, youtube_video_id=None),
                     _song("Migration", artist=artist, youtube_video_id="v2", kind=SONG_KIND_FULL_ALBUM)])
    session.commit()

    assert fac.unmarked_video_ids(session) == ["v1"]
