"""Splitting joined artist names ("Sw@da x Maxim", "A feat. B") into
separate artists: the parser, candidate finding, applying a split, the ignore
list and the batch review. In-memory SQLite."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import artist_splitting as split
from utils.database.datatables import AdditionalSongArtist, Artist, Base, Song
from utils.database.song_artists import add_additional_artist, song_artist_label
from utils.ui import artist_split_review as review


# (name, expected main, expected feat, expected confident) - None: not split
PARSE_CASES = [
    ("Sw@da x Maxim x Niczos", ["Sw@da", "Maxim", "Niczos"], [], True),
    ("Futro Ft. Fisz & Novika", ["Futro"], ["Fisz", "Novika"], True),
    ("BT Featuring Mike Doughty", ["BT"], ["Mike Doughty"], True),
    ("Bad Bunny (ft. Bomba Estéreo)", ["Bad Bunny"], ["Bomba Estéreo"], True),
    ("Waglewski, Fisz, Emade", ["Waglewski", "Fisz", "Emade"], [], False),
    ("Simon & Garfunkel", ["Simon", "Garfunkel"], [], False),
    ("Ben Folds/Nick Hornby", ["Ben Folds", "Nick Hornby"], [], False),
    ("KLEKS/IGO/Mrozu", ["KLEKS", "IGO", "Mrozu"], [], False),
    ("Big Brother & the Holding Company", ["Big Brother", "the Holding Company"], [], False),
    ("Pezet - Małolat feat. Małpa", ["Pezet - Małolat"], ["Małpa"], False),
    ("Lisa Gerrard, Hans Zimmer, Lisa Gerrard", ["Lisa Gerrard", "Hans Zimmer"], [], False),
    # one artist, never split
    ("Final Fantasy X OST", None, None, None),
    ("Malcolm X", None, None, None),
    ("AC/DC", None, None, None),
    ("C+C Music Factory", None, None, None),
    ("2 tm 2,3", None, None, None),
    ("Lucie,Too", None, None, None),
]


@pytest.mark.parametrize("name, main, feat, confident", PARSE_CASES, ids=[c[0] for c in PARSE_CASES])
def test_propose_artist_split(name, main, feat, confident):
    proposal = split.propose_artist_split(name)
    if main is None:
        assert proposal is None
        return
    assert (proposal.main, proposal.feat, proposal.confident) == (main, feat, confident)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def ignore_file(tmp_path, monkeypatch):
    path = tmp_path / "artist_split_ignore.json"
    monkeypatch.setattr(split, "_IGNORE_PATH", path)
    return path


def _artist_with_song(session, name, title="Song", origin=None):
    artist = Artist(name=name, origin=origin)
    session.add(Song(title=title, artist=artist))
    session.flush()
    return artist


def test_candidates_reuse_existing_parts_and_preselect_when_all_exist(session):
    _artist_with_song(session, "Daria Zawiałow", "Solo")
    _artist_with_song(session, "Artur Rojek", "Solo 2")
    joined = _artist_with_song(session, "Daria Zawiałow, Artur Rojek", "Duet")
    unknown = _artist_with_song(session, "Alex & Sierra", "Little Do You Know")
    session.add(Artist(name="Nobody & Nothing"))  # no songs, nothing to move
    session.flush()

    candidates = {c.artist.name: c for c in split.find_split_candidates(session)}
    assert set(candidates) == {joined.name, unknown.name}
    assert set(candidates[joined.name].existing) == {"Daria Zawiałow", "Artur Rojek"}
    assert candidates[joined.name].preselected
    assert not candidates[unknown.name].preselected


def test_ignore_list_hides_names_case_insensitively(session, ignore_file):
    _artist_with_song(session, "Simon & Garfunkel")
    split.add_to_split_ignore_list(["simon & GARFUNKEL"])
    assert ignore_file.exists()
    assert split.find_split_candidates(session) == []


def test_apply_moves_songs_creates_missing_parts_and_deletes_joined_row(session):
    fisz = _artist_with_song(session, "Fisz", "Solo")
    joined = _artist_with_song(session, "Futro Ft. Fisz & Novika", "Track", origin="PL")
    joined_id = joined.id
    (candidate,) = split.find_split_candidates(session, [joined])

    split.apply_artist_split(session, candidate)
    session.commit()

    song = session.query(Song).filter_by(title="Track").one()
    assert song.artist.name == "Futro"
    assert song.artist.origin == "PL"  # new parts inherit the joined row's origin
    assert song_artist_label(song) == "Futro feat. Fisz, Novika"
    assert any(link.artist is fisz for link in song.additional_artist_links)  # reused, not duplicated
    assert session.get(Artist, joined_id) is None
    assert session.query(Artist).filter_by(name="Fisz").count() == 1


def test_batch_shares_newly_created_parts_between_joined_names(session):
    a = _artist_with_song(session, "Bass Astral x Igo", "One")
    b = _artist_with_song(session, "Bass Astral x Mrozu", "Two")
    created = {}
    for candidate in split.find_split_candidates(session, [a, b]):
        split.apply_artist_split(session, candidate, created)
    session.commit()
    assert session.query(Artist).filter_by(name="Bass Astral").count() == 1


def test_apply_replaces_joined_artist_used_as_an_additional_artist(session):
    host = _artist_with_song(session, "Host", "Host Song")
    joined = _artist_with_song(session, "A x B", "Own")
    host_song = host.songs[0]
    add_additional_artist(host_song, joined, "feat")
    session.flush()
    (candidate,) = split.find_split_candidates(session, [joined])

    split.apply_artist_split(session, candidate)
    session.commit()

    assert song_artist_label(host_song) == "Host feat. A, B"
    assert session.query(AdditionalSongArtist).filter_by(artist_id=joined.id).count() == 0


def test_review_applies_ticked_names_and_remembers_the_rest(session, monkeypatch):
    _artist_with_song(session, "Sw@da x Maxim", "Bahato")
    _artist_with_song(session, "Simon & Garfunkel", "The Boxer")

    class Answer:
        def __init__(self, value):
            self.value = value

        def ask(self):
            return self.value

    seen = {}

    def fake_checkbox(message, choices):
        seen["checked"] = {c.title.split("  ->")[0]: c.checked for c in choices}
        return Answer([c.value for c in choices if c.checked])

    monkeypatch.setattr(review, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(review, "submit_global_database_session", session.commit)
    monkeypatch.setattr(review.questionary, "checkbox", fake_checkbox)
    monkeypatch.setattr(review.questionary, "confirm", lambda message, default=False: Answer(True))

    assert review.review_artist_splits() == 1
    assert seen["checked"] == {"Simon & Garfunkel": False, "Sw@da x Maxim": True}
    assert session.query(Song).filter_by(title="Bahato").one().artist.name == "Sw@da"
    assert session.query(Artist).filter_by(name="Simon & Garfunkel").count() == 1
    assert "simon garfunkel" in split.load_split_ignore_list()
