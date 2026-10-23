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
    monkeypatch.setattr(module, "open_global_driver", lambda: None)
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

    def discover(song, modules):
        try:
            return next(answers)
        except StopIteration:
            raise KeyboardInterrupt

    monkeypatch.setattr(module, "discover_album_name", discover)

    module.fill_missing_albums()

    assert edits == [("One", "album", "Album One")]


def test_years_found_before_ctrl_c_are_saved(monkeypatch):
    module = importlib.import_module(f"{_PKG}.fill_missing_years")
    edits = []
    _common(monkeypatch, module, edits)
    songs = [_song("One", album="First"), _song("Two", album="Second")]
    monkeypatch.setattr(module, "get_songs_with_album_missing_year", lambda: songs)
    monkeypatch.setattr(module, "_fill_years_known_in_database", lambda songs: [])
    monkeypatch.setattr(module, "load_year_discovery_modules", lambda: ["module"])
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
