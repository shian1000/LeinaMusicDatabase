"""One artist under two spellings, and songs with artist and title swapped
(utils/database/artist_identity.py) - what "Resolve albums with several
artists" offers to fix before settling an album title. Name pairs are real
ones from that list."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import albums as al
from utils.database.artist_identity import (
    artists_look_same,
    find_same_artist_pairs,
    find_swapped_songs,
    fix_swapped_song,
)
from utils.database.datatables import Artist, Base, Song


def _artist(name, synonyms=None, artist_id=None):
    return SimpleNamespace(id=artist_id or hash(name), name=name, synonyms=synonyms)


@pytest.mark.parametrize("a, b", [
    ("björk", "Вjörk"),                                  # Cyrillic В typed for B
    ("P.O.D.", "POD"),
    ("Man I Trust", "Men I Trust"),
    ("Taeko Ohnuki", "Taeko Onuki"),
    ("Aimyon", "あいみょん"),
    ("Molchat Doma", "Molchat Doma / Молчат Дома"),
    ("Molchat Doma", "Молчат Дома (Molchat Doma)"),
    ("Chylińska", "Agnieszka Chylińska"),
    ("Mariya Takeuchi", "Mariya Takeuchi 竹内 まりや"),
])
def test_same_artist_under_two_spellings(a, b):
    assert artists_look_same(_artist(a), _artist(b))


@pytest.mark.parametrize("a, b", [
    ("Fisz", "Fisz Emade Tworzywo"),                     # another act; a merge would move Fisz's solo songs
    ("sanah", "sanah i Igor Herbut"),                    # a duo, for "Split joined artist names"
    ("Dawid Podsiadło", "Dawid Podsiadło i Kaśka Sochacka"),
    ("Purple Disco Machine", "Purple Disco Machine, Sophie and the Giants"),
    ("Persona 5 OST", "Persona 5 Strikers OST"),         # 0.74 similar, two games
    ("Genesis", "Kygo"),
])
def test_different_artists_are_not_offered_for_merging(a, b):
    assert not artists_look_same(_artist(a), _artist(b))


def test_a_synonym_counts():
    assert artists_look_same(_artist("Бумбокс", synonyms="BoomBox, Boombox"), _artist("BoomBox"))


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    al.register_album_sync(factory)
    s = factory()
    yield s
    s.close()


def _song(session, artist, title, album):
    song = Song(title=title, artist=artist, album=album)
    session.add(song)
    session.flush()
    return song


def test_swapped_song_is_flagged_and_fixed_onto_the_existing_artist(session):
    styx, renegade = Artist(name="Styx"), Artist(name="Renegade")
    session.add_all([styx, renegade])
    real = _song(session, styx, "Renegade", "Pieces of Eight")
    _song(session, styx, "Blue Collar Man", "Pieces of Eight")
    swapped = _song(session, renegade, "Styx", "Pieces of Eight")
    session.commit()

    assert find_swapped_songs([real, swapped]) == [(swapped, styx)]

    removed = fix_swapped_song(session, swapped, styx)
    session.commit()
    assert removed is renegade and session.get(Artist, renegade.id) is None
    assert (swapped.artist.name, swapped.title) == ("Styx", "Renegade")
    # One artist on the title now: linking (which the review runs after a fix) gives both one album
    al.link_unassigned_songs(session)
    session.commit()
    assert swapped.album_id is not None and swapped.album_id == real.album_id


def test_mirrored_pair_with_one_song_each_flags_only_one(session):
    # "Renegade - Styx" and "Styx - Renegade", each artist with just that song
    styx, renegade = Artist(name="Styx"), Artist(name="Renegade")
    session.add_all([styx, renegade])
    a = _song(session, renegade, "Styx", "Pieces of Eight")
    b = _song(session, styx, "Renegade", "Pieces of Eight")
    session.commit()
    assert len(find_swapped_songs([a, b])) == 1


def test_real_song_of_the_bigger_artist_is_not_flagged(session):
    natalia, junk = Artist(name="Natalia Przybysz"), Artist(name="Dzieci Malarzy")
    session.add_all([natalia, junk])
    real = _song(session, natalia, "Dzieci malarzy", "Światło nocne")
    _song(session, natalia, "Światło nocne", "Światło nocne")
    swapped = _song(session, junk, "Natalia Przybysz", "Światło nocne")
    session.commit()
    assert find_swapped_songs([real, swapped]) == [(swapped, natalia)]


def test_same_artist_pairs_lists_each_pair_once(session):
    bjork, typo, other = Artist(name="björk"), Artist(name="Вjörk"), Artist(name="Kygo")
    session.add_all([bjork, typo, other])
    songs = [_song(session, bjork, "Army of Me", "Post"), _song(session, typo, "Hyperballad", "Post"),
             _song(session, bjork, "I Miss You", "Post"), _song(session, other, "Firestone", "Post")]
    session.commit()
    assert find_same_artist_pairs(songs) == [(bjork, typo)]
