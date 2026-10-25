"""The import engine shared by the mp3 and YouTube playlist imports
(utils/discoveries/import_engine.py run_import_batch()), end to end on a
temp-file music.db: new artists and songs created, existing ones reused
(case-insensitively; "Song" is "Song (Remastered)"), a YouTube link filled in
for a song that had none, entries without an artist or title skipped, one new
artist per name in a batch, similar songs and MusicBrainz-corrected artists
asked about only after the whole batch (once per artist name), late entries,
full albums kept apart from tracks, and only the batch's new artists offered
for splitting. MusicBrainz, the split and album reviews are faked."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from config.constants import SONG_KIND_FULL_ALBUM
from utils.database import database_sessions
from utils.database.datatables import Artist, Base, Song
from utils.discoveries import import_engine as ie


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(database_sessions, "_GLOBAL_DATABASE_SESSION", (session, None))
    monkeypatch.setattr(ie, "_SPELL_CHECK_CACHE", {})
    monkeypatch.setattr(ie.spellcheck_cache, "save", lambda: None)

    spelling = {}  # artist (or (artist, title)) -> what MusicBrainz "corrects" it to
    spell_calls = []

    def check_spelling(artist, title):
        spell_calls.append((artist, title))
        found = spelling.get((artist, title)) or spelling.get(artist)
        return found or {"found": False}

    monkeypatch.setattr(ie, "check_spelling", check_spelling)
    answers, questions = [], []

    def ask_yes_no(question):
        questions.append(question)
        return answers.pop(0)

    monkeypatch.setattr(ie, "ask_yes_no", ask_yes_no)
    split_offers, album_offers = [], []
    monkeypatch.setattr(ie, "review_artist_splits", lambda artists: split_offers.append(list(artists)))
    monkeypatch.setattr(ie, "offer_album_resolution", lambda songs: album_offers.append(list(songs)))
    yield {"session": session, "spelling": spelling, "spell_calls": spell_calls, "answers": answers,
           "questions": questions, "split_offers": split_offers, "album_offers": album_offers}
    session.close()


def _artist(session, name, *titles, **kw):
    artist = Artist(name=name, **kw)
    session.add(artist)
    for title in titles:
        session.add(Song(title=title, artist=artist))
    session.commit()
    return artist


def _entry(artist, title, **kw):
    return {"artist_name": artist, "title": title, **kw}


def _songs(session):
    return sorted((s.artist.name, s.title) for s in session.query(Song))


# ==================== creating and reusing ====================

def test_a_new_artist_and_song_are_created(env):
    session = env["session"]

    added = ie.run_import_batch([_entry("Rammstein", "Sonne", album="Mutter", year="2001", origin="Germany",
                                        language="de", youtube_video_id="StZcUAPRRac")])

    [song] = added
    assert (song.title, song.album, song.year, song.language, song.youtube_video_id) == (
        "Sonne", "Mutter", 2001, "de", "StZcUAPRRac")
    assert (song.artist.name, song.artist.origin) == ("Rammstein", "Germany")
    assert song.kind is None
    assert env["split_offers"] == [[song.artist]]
    assert env["album_offers"] == [[song]]
    assert env["questions"] == []


def test_feat_artists_are_credited_on_the_new_song(env):
    session = env["session"]
    _artist(session, "Bruno Mars", "Grenade")

    [song] = ie.run_import_batch([_entry("Travie McCoy", "Billionaire", feat_artists=["Bruno Mars", "Someone New"])])

    assert song.title == "Billionaire"
    assert [(link.artist.name, link.role) for link in song.additional_artist_links] == [
        ("Bruno Mars", "feat"), ("Someone New", "feat")]
    assert session.query(Artist).filter(Artist.name == "Bruno Mars").count() == 1


def test_a_year_that_isnt_a_number_is_left_empty(env):
    [song] = ie.run_import_batch([_entry("Rammstein", "Sonne", year="2001?")])

    assert song.year is None


def test_an_existing_artist_is_reused_whatever_the_case(env):
    rammstein = _artist(env["session"], "Rammstein", "Engel")

    [song] = ie.run_import_batch([_entry("RAMMSTEIN", "Sonne")])

    assert song.artist is rammstein
    assert env["session"].query(Artist).count() == 1
    assert env["split_offers"] == []  # only the batch's new artists are offered


@pytest.mark.parametrize("title", ["Engel", "engel", "Engel (Remastered)"])
def test_an_existing_song_is_skipped_and_gets_the_link_it_lacked(env, title, capsys):
    session = env["session"]
    _artist(session, "Rammstein", title)

    added = ie.run_import_batch([_entry("Rammstein", "Engel", youtube_video_id="x2rQzv8OWEY")])

    assert added == []
    [song] = session.query(Song).all()
    assert song.youtube_video_id == "x2rQzv8OWEY"
    assert "Rammstein - Engel (reason - Song already exists)" in capsys.readouterr().out


def test_an_existing_link_is_never_overwritten(env):
    session = env["session"]
    rammstein = _artist(session, "Rammstein")
    session.add(Song(title="Engel", artist=rammstein, youtube_video_id="old"))
    session.commit()

    ie.run_import_batch([_entry("Rammstein", "Engel", youtube_video_id="new")])

    assert session.query(Song).one().youtube_video_id == "old"


def test_entries_without_an_artist_or_title_are_skipped_with_the_callers_skips(env, capsys):
    added = ie.run_import_batch(
        [_entry("", "Sonne"), _entry("Rammstein", None, _label="track03.mp3"), _entry("Rammstein", "Sonne")],
        pre_skipped=[("broken.mp3", "No tags")])

    assert [s.title for s in added] == ["Sonne"]
    out = capsys.readouterr().out
    assert "Skipped 3 songs:" in out
    assert "broken.mp3 (reason - No tags)" in out
    assert " - Sonne (reason - Missing artist or title)" in out
    assert "track03.mp3 (reason - Missing artist or title)" in out


def test_one_new_artist_per_name_in_a_batch(env):
    added = ie.run_import_batch([_entry("Rammstein", "Sonne"), _entry("rammstein", "Engel")])

    assert env["session"].query(Artist).count() == 1
    assert {s.artist.name for s in added} == {"Rammstein"}


def test_the_added_songs_come_back_by_artist_and_title(env):
    added = ie.run_import_batch([_entry("Rammstein", "Sonne"), _entry("AURORA", "Runaway"),
                                 _entry("Rammstein", "Engel")])

    assert [(s.artist.name, s.title) for s in added] == [("AURORA", "Runaway"), ("Rammstein", "Engel"),
                                                         ("Rammstein", "Sonne")]


# ==================== similar songs ====================

@pytest.mark.parametrize("use_existing", [True, False])
def test_a_similar_song_is_asked_about_after_the_batch(env, use_existing, capsys):
    session = env["session"]
    _artist(session, "Rammstein", "Du hast")
    env["answers"].append(use_existing)

    added = ie.run_import_batch([_entry("Rammstein", "Du hasst", youtube_video_id="W3q8Od5qJio"),
                                 _entry("Rammstein", "Sonne")])

    out = capsys.readouterr().out
    # every entry went through before the question
    assert out.index("Rammstein - Sonne") < out.index("REVIEW: 1 song(s) found a similar match")
    assert env["questions"] == ["Do you wish to use the song already in the database?"]
    existing = session.query(Song).filter_by(title="Du hast").one()
    if use_existing:
        assert [s.title for s in added] == ["Sonne"]
        assert existing.youtube_video_id == "W3q8Od5qJio"
        assert "Rammstein - Du hasst (reason - Song already exists)" in out
    else:
        assert [s.title for s in added] == ["Du hasst", "Sonne"]
        assert existing.youtube_video_id is None
    assert len(env["album_offers"][0]) == len(added)


def test_a_new_song_by_a_new_artist_asks_musicbrainz_and_nothing_else(env):
    ie.run_import_batch([_entry("Rammstein", "Sonne")])

    assert ("Rammstein", "Sonne") in env["spell_calls"]
    assert env["questions"] == []


# ==================== similar artists ====================

def test_a_corrected_artist_is_asked_about_once_after_the_batch(env, capsys):
    session = env["session"]
    rammstein = _artist(session, "Rammstein", "Engel")
    env["spelling"]["Ramstein"] = {"found": True, "corrected_artist": "Rammstein", "corrected_title": ""}
    env["answers"].append(True)

    added = ie.run_import_batch([_entry("Ramstein", "Sonne"), _entry("AURORA", "Runaway"),
                                 _entry("Ramstein", "Mutter")])

    out = capsys.readouterr().out
    assert out.index("AURORA - Runaway") < out.index("REVIEW: 1 artist(s) found a similar match")
    assert env["questions"] == ["Do you wish to use the artist already in the database?"]
    assert sorted((s.artist.name, s.title) for s in added) == [
        ("AURORA", "Runaway"), ("Rammstein", "Mutter"), ("Rammstein", "Sonne")]
    assert {s.artist for s in added if s.title != "Runaway"} == {rammstein}
    assert [a.name for a in env["split_offers"][0]] == ["AURORA"]


def test_refusing_the_corrected_artist_creates_the_new_one(env):
    session = env["session"]
    _artist(session, "Rammstein", "Engel")
    env["spelling"]["Ramstein"] = {"found": True, "corrected_artist": "Rammstein", "corrected_title": ""}
    env["answers"].append(False)

    added = ie.run_import_batch([_entry("Ramstein", "Sonne", origin="Germany"), _entry("Ramstein", "Mutter")])

    new = session.query(Artist).filter_by(name="Ramstein").one()
    assert new.origin == "Germany"
    assert {s.artist for s in added} == {new} and len(added) == 2
    assert env["split_offers"] == [[new]]


def test_a_correction_unlike_the_name_is_not_asked(env):
    _artist(env["session"], "Rammstein", "Engel")
    env["spelling"]["Lindemann"] = {"found": True, "corrected_artist": "Rammstein", "corrected_title": ""}

    [song] = ie.run_import_batch([_entry("Lindemann", "Steh auf")])

    assert song.artist.name == "Lindemann"
    assert env["questions"] == []


def test_a_song_of_a_deferred_artist_can_still_be_a_similar_song(env):
    session = env["session"]
    _artist(session, "Rammstein", "Du hast")
    env["spelling"]["Ramstein"] = {"found": True, "corrected_artist": "Rammstein", "corrected_title": ""}
    env["answers"].extend([True, True])  # use Rammstein, then use "Du hast"

    added = ie.run_import_batch([_entry("Ramstein", "Du hasst")])

    assert added == []
    assert env["questions"] == ["Do you wish to use the artist already in the database?",
                                "Do you wish to use the song already in the database?"]
    assert _songs(session) == [("Rammstein", "Du hast")]


# ==================== late entries ====================

def test_late_entries_run_after_the_main_loop_and_before_the_reviews(env, capsys):
    session = env["session"]
    _artist(session, "Rammstein", "Du hast")
    seen = []

    def late_entries():
        seen.append(_songs(session))
        return [_entry("Rammstein", "Du hasst")], [("swapped.mp3", "Left out in the swap review")]

    env["answers"].append(False)
    added = ie.run_import_batch([_entry("AURORA", "Runaway")], late_entries=late_entries)

    assert seen == [[("AURORA", "Runaway"), ("Rammstein", "Du hast")]]
    assert [s.title for s in added] == ["Runaway", "Du hasst"]
    out = capsys.readouterr().out
    assert "REVIEW: 1 song(s) found a similar match" in out
    assert "swapped.mp3 (reason - Left out in the swap review)" in out


# ==================== full albums ====================

def test_a_title_saying_full_album_makes_a_full_album(env):
    [song] = ie.run_import_batch([_entry("Bonobo", "Migration (Full Album)")])

    assert (song.kind, song.album) == (SONG_KIND_FULL_ALBUM, "Migration")


def test_a_track_named_like_a_full_album_is_a_new_song(env):
    """Not even offered as a similar song (find_similar_song() goes through songs_for_entry())."""
    session = env["session"]
    bonobo = _artist(session, "Bonobo")
    session.add(Song(title="Migration", artist=bonobo, kind=SONG_KIND_FULL_ALBUM))
    session.commit()

    [song] = ie.run_import_batch([_entry("Bonobo", "Migration", duration_s=322)])

    assert song.kind is None
    assert session.query(Song).count() == 2
    assert env["questions"] == []


def test_the_full_album_entry_finds_the_full_album(env):
    session = env["session"]
    bonobo = _artist(session, "Bonobo", "Migration")
    session.add(Song(title="Migration", artist=bonobo, kind=SONG_KIND_FULL_ALBUM, album="Migration"))
    session.commit()

    added = ie.run_import_batch([_entry("Bonobo", "Migration (Full Album)", youtube_video_id="album")])

    assert added == []
    full = session.query(Song).filter_by(kind=SONG_KIND_FULL_ALBUM).one()
    track = session.query(Song).filter(Song.kind.is_(None)).one()
    assert (full.youtube_video_id, track.youtube_video_id) == ("album", None)
