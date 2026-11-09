"""Covers (songs.covered_artist_id, migration 0020; utils/common/cover_names.py,
utils/database/covers.py, sanitize's "cover_credit" step)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import menu.main_menu  # noqa: F401 - import order, see AGENTS.md
from utils.common.cover_names import read_cover, title_with_cover
from utils.common import sanitize as s
from utils.database import covers
from utils.database.artist_reassignment import is_unused
from utils.database.datatables import Artist, Base, Song
from utils.database.song_artists import reassign_additional_artist_links


@pytest.mark.parametrize("artist, title, expected", [
    ("Ørganek", "Creep (Radiohead cover)", ("Creep", "Radiohead", None)),
    ("Jennel Garcia", "Toxic (Britney Spears acoustic cover)", ("Toxic", "Britney Spears", None)),
    ("Radio Tapok", "Feel it still (cover Portugal the man)", ("Feel it still", "Portugal the man", None)),
    ("Robyn", "Chop Suey (System of a Down) Jazz Cover", ("Chop Suey", "System of a Down", None)),
    ("Chris Brown", "Look At Me Now (Cover by Karmin)", ("Look At Me Now", "Chris Brown", "Karmin")),
    ("annapantsu", "Gravity (Sara Bareilles) 【covered by Anna】", ("Gravity", "Sara Bareilles", None)),
    ("AURORA", "Rasputin (Boney M cover - acoustic / live)", ("Rasputin", "Boney M", None)),
    ("HSCC", "Just the Two of Us (Cover)", ("Just the Two of Us", None, None)),
    ("Alina Gingertail", "Dirge (Gingertail Cover)", ("Dirge", None, None)),
])
def test_read_cover(artist, title, expected):
    reading = read_cover(artist, title)
    assert (reading.title, reading.covered, reading.performer) == expected


@pytest.mark.parametrize("title", ["Recover", "Twinz (Deep Cover ’98)", "Discovery", "Take Cover", None])
def test_not_a_cover(title):
    assert read_cover("A", title) is None


def test_a_credit_without_brackets_needs_a_known_artist():
    known = {"beyonce": "Beyoncé"}.get
    reading = read_cover("Hannah Rue", "Crazy in Love (Remix 2014) Beyonce Cover", lambda n: known(n.lower()))
    assert (reading.title, reading.covered) == ("Crazy in Love (Remix 2014)", "Beyoncé")
    assert read_cover("A", "Rolling Keys Cover").covered is None


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        yield db


def _song(session, artist, title):
    row = session.query(Artist).filter_by(name=artist).first() or Artist(name=artist)
    song = Song(title=title, artist=row)
    session.add(song)
    session.flush()
    return song


def test_proposal_marks_the_song_and_shows_the_credit(session):
    _song(session, "Radiohead", "Karma Police")
    song = _song(session, "Ørganek", "Creep (Radiohead cover)")
    proposals, unnamed = covers.find_covers(session)
    assert [p.certain for p in proposals] == [True] and not unnamed
    covers.apply_cover_proposal(session, proposals[0])
    assert (song.title, song.covered_artist.name) == ("Creep", "Radiohead")
    assert title_with_cover(song) == "Creep (Radiohead cover)"
    assert read_cover("Ørganek", title_with_cover(song)).covered == "Radiohead"


def test_cover_by_moves_the_song_to_the_performer(session):
    song = _song(session, "Chris Brown", "Look At Me Now (Cover by Karmin)")
    proposals, _ = covers.find_covers(session)
    covers.apply_cover_proposal(session, proposals[0])
    assert (song.artist.name, song.covered_artist.name, song.title) == ("Karmin", "Chris Brown", "Look At Me Now")


def test_a_covered_artist_is_kept_and_follows_a_merge(session):
    original = Artist(name="Rihanna")
    twin = Artist(name="Rhianna")
    session.add_all([original, twin])
    song = _song(session, "Walk off the Earth", "Man Down")
    covers.set_covered_artist([song], twin)
    session.flush()
    assert not is_unused(session, twin)
    reassign_additional_artist_links(session, twin.id, original.id)
    assert song.covered_artist is original


def test_a_song_cannot_cover_its_own_artist(session):
    song = _song(session, "A", "B")
    with pytest.raises(ValueError):
        covers.set_covered_artist([song], song.artist)


def test_import_reads_the_credit():
    metadata = s.sanitize_metadata({"artist_name": "Ørganek", "title": "Creep (Radiohead cover)"}, s.SOURCE_YOUTUBE)
    assert (metadata["title"], metadata["covered_artist"]) == ("Creep", "Radiohead")
