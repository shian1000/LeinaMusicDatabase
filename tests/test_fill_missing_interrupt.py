"""Fill missing data → Albums / Years: Ctrl+C during the slow lookups keeps
what was found so far (UI-14). Every lookup, prompt and DB write mocked."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

import menu.main_menu  # noqa: F401,E402  (import order, see AGENTS.md)

_PKG = "menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data"


def _song(title, album=None):
    return SimpleNamespace(id=title, title=title, album=album, year=None,
                           artist=SimpleNamespace(name="Artist"), artist_id=1)


def _common(monkeypatch, module, edits):
    monkeypatch.setattr(module, "close_global_driver", lambda: None)
    monkeypatch.setattr(module, "load_max_songs_per_fetch", lambda: 100)
    monkeypatch.setattr(module, "ask_yes_no", lambda q: False)
    monkeypatch.setattr(module, "submit_global_database_session", lambda: None)
    monkeypatch.setattr(module, "edit_db_entry", lambda song, category, value: edits.append((song.title, category, value)))


def test_albums_found_before_ctrl_c_are_saved(monkeypatch):
    module = importlib.import_module(f"{_PKG}.fill_missing_albums")
    edits = []
    _common(monkeypatch, module, edits)
    songs = [_song("One"), _song("Two"), _song("Three")]
    monkeypatch.setattr(module, "get_songs_with_empty_category", lambda category: songs)
    monkeypatch.setattr(module, "load_discovery_modules", lambda: [])
    answers = iter(["Album One"])

    def discover(song, modules, cache=None):
        try:
            return SimpleNamespace(album=next(answers), track_number=None, disc_number=None)
        except StopIteration:
            raise KeyboardInterrupt

    monkeypatch.setattr(module, "discover_album", discover)

    module.fill_missing_albums()

    assert edits == [("One", "album", "Album One")]


def test_years_found_before_ctrl_c_are_saved(monkeypatch):
    module = importlib.import_module(f"{_PKG}.fill_missing_years")
    edits = []
    _common(monkeypatch, module, edits)
    songs = [_song("One", album="First"), _song("Two", album="Second")]
    monkeypatch.setattr(module, "get_songs_with_album_missing_year", lambda: songs)
    monkeypatch.setattr(module, "_fill_years_known_in_database", lambda songs: [])
    monkeypatch.setattr(module, "load_discovery_modules", lambda kind: ["module"])
    monkeypatch.setattr(module, "release_year_from_stored_link", lambda songs: None)
    monkeypatch.setattr(module, "artist_match_synonyms", lambda artist: [])
    monkeypatch.setattr(module, "cyrillic_search_variant", lambda *a: None)
    answers = iter([SimpleNamespace(year=1999, needs_review=False, source_url=None)])

    def discover(*args, **kwargs):
        try:
            return next(answers)
        except StopIteration:
            raise KeyboardInterrupt

    monkeypatch.setattr(module, "discover_release_year_result", discover)

    module.fill_missing_years()

    assert edits == [("One", "year", "1999")]


# ==================== Years for an import's songs, with their entries ====================

def test_a_title_year_is_offered_when_no_module_finds_one(monkeypatch, capsys):
    """Years for the songs an import just added (`songs` + `entries`): a year
    the cleanup cut from a title goes to the unsure-years review when the
    modules find nothing; a module's year still wins; a group whose titles
    name different years, or no year, is left for the manual step."""
    from utils.common import sanitize as s
    from utils.common.song_entry import SongEntry
    module = importlib.import_module(f"{_PKG}.fill_missing_years")
    edits = []
    _common(monkeypatch, module, edits)
    monkeypatch.setattr(module, "get_songs_with_album_missing_year",
                        lambda: (_ for _ in ()).throw(AssertionError("the whole catalog was read")))
    monkeypatch.setattr(module, "_fill_years_known_in_database", lambda songs: [])
    monkeypatch.setattr(module, "load_discovery_modules", lambda kind: ["module"])
    monkeypatch.setattr(module, "release_year_from_stored_link", lambda songs: None)
    monkeypatch.setattr(module, "artist_match_synonyms", lambda artist: [])
    monkeypatch.setattr(module, "cyrillic_search_variant", lambda *a: None)
    monkeypatch.setattr(module, "_finish", lambda touched: None)
    found = {"Found": SimpleNamespace(year=1999, needs_review=False, source_url=None)}
    monkeypatch.setattr(module, "discover_release_year_result", lambda artist, query, *a, **k: found.get(query))
    shown = []
    monkeypatch.setattr(module.questionary, "checkbox",
                        lambda message, choices: shown.extend(c.title for c in choices) or SimpleNamespace(ask=lambda: [0]))

    def read(title):
        return s.sanitize_metadata(SongEntry({"artist_name": "Artist", "title": title}, raw_text=title,
                                             source=s.SOURCE_YOUTUBE), s.SOURCE_YOUTUBE)

    legenda, found_song, plain = _song("Legenda", "Legenda"), _song("Found", "Found"), _song("Plain", "Plain")
    no_album, has_year = _song("Old (1980)"), _song("Done", "Done")
    has_year.year = 2001
    entries = {"Legenda": read("Legenda (1991)"), "Found": read("Found (2010)"), "Plain": read("Plain")}

    module.fill_missing_years([legenda, found_song, plain, no_album, has_year], entries)

    assert shown == ['Artist - Legenda: 1991  (the title "Legenda (1991)")']
    assert sorted(edits) == [("Found", "year", "1999"), ("Legenda", "year", "1991")]
    out = capsys.readouterr().out
    assert "1991" in out and "from the title for" in out


# ==================== the end of an import: what to fill in ====================

def test_the_end_of_an_import_offers_albums_and_years_for_its_songs(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from utils.database.datatables import Artist, Base, Song
    from utils.discoveries.import_engine import ImportResult
    package = importlib.import_module(_PKG)
    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    artist = Artist(name="Artist")
    new, found, done = (Song(title="New", artist=artist), Song(title="Found", album="Album", artist=artist),
                        Song(title="Done", album="Album", year=2000, artist=artist))
    session.add_all([new, found, done])
    session.commit()
    monkeypatch.setattr(package, "require_global_database_sessions", lambda: (session, None))
    calls, offered = [], []
    monkeypatch.setattr(package, "fill_missing_albums", lambda songs, entries: calls.append(("albums", songs, entries)))
    monkeypatch.setattr(package, "fill_missing_years", lambda songs, entries: calls.append(("years", songs, entries)))

    def checkbox(message, choices):
        offered.append([(c.title, c.value) for c in choices])
        return SimpleNamespace(ask=lambda: picked)

    monkeypatch.setattr(package.questionary, "checkbox", checkbox)
    entries = {new.id: "entry of New", found.id: "entry of Found", done.id: "entry of Done"}
    result = ImportResult([new], entries)

    picked = ["albums", "years"]
    package.fill_missing_for_import(result)
    assert offered[-1] == [("Albums (1 song(s) without one)", "albums"),
                           ("Years (2 song(s) without one, looked up for those with an album)", "years")]
    # Albums first: Years looks at songs with an album; the import's songs, added or found, with their entries
    assert [(step, [s.title for s in songs], got) for step, songs, got in calls] == [
        ("albums", ["Done", "Found", "New"], entries), ("years", ["Done", "Found", "New"], entries)]

    calls.clear()
    picked = []  # nothing ticked: nothing run
    package.fill_missing_for_import(result)
    assert calls == []
    package.fill_missing_for_import(ImportResult([]))  # nothing imported: not even asked
    assert len(offered) == 2


def test_the_songs_added_from_requests_are_offered_their_data(monkeypatch):
    from utils.ui import inbox_request_review as review
    from utils.discoveries import import_engine
    monkeypatch.setattr(import_engine, "run_import_batch", lambda entries: ("result", len(entries)))
    monkeypatch.setattr(review, "submit_global_database_session", lambda: None)
    monkeypatch.setattr(review, "mark_song_added", lambda case, artist, title: None)
    case = SimpleNamespace(metadata=lambda artist, title: {"artist_name": artist, "title": title})
    got = []
    review._add_requested_songs([(case, "A", "T")], got.append)
    assert got == [("result", 1)]
    review._add_requested_songs([(case, "A", "T")])  # without the menu's offer: as before
