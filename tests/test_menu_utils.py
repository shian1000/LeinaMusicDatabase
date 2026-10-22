"""utils/ui/menu_utils.py pickers with mocked prompts: Esc / Ctrl+C (questionary's
None) and empty lists end the pick cleanly."""
import pytest

from utils.database.datatables import Artist
from utils.ui import menu_utils


def _answer(monkeypatch, *answers):
    answers = iter(answers)
    monkeypatch.setattr(menu_utils.questionary, "select",
                        lambda *a, **k: type("Q", (), {"ask": lambda self: next(answers)})())


def test_pick_from_db_objects_after_esc_or_ctrl_c_is_none(monkeypatch):
    monkeypatch.setattr(menu_utils, "extract_db_object_info", lambda objects, fields: [(a.name,) for a in objects])
    _answer(monkeypatch, None)
    assert menu_utils.pick_from_db_objects([Artist(name="ABBA")]) is None


def test_pick_from_db_objects_with_nothing_to_pick_is_none(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("nothing to ask about")

    monkeypatch.setattr(menu_utils.questionary, "select", fail)
    assert menu_utils.pick_from_db_objects([]) is None


def test_pick_from_db_objects_still_picks(monkeypatch):
    abba, queen = Artist(name="ABBA"), Artist(name="Queen")
    monkeypatch.setattr(menu_utils, "extract_db_object_info", lambda objects, fields: [(a.name,) for a in objects])
    _answer(monkeypatch, 1)
    assert menu_utils.pick_from_db_objects([abba, queen]) is queen


def test_the_file_browser_after_esc_or_ctrl_c_is_none(monkeypatch, tmp_path):
    (tmp_path / "music").mkdir()
    _answer(monkeypatch, "music", None)
    assert menu_utils.open_file_browser_terminal(str(tmp_path)) is None


def test_the_file_browser_selects_a_folder_it_can_open(monkeypatch, tmp_path):
    (tmp_path / "music").mkdir()
    _answer(monkeypatch, "music", menu_utils.FILE_BROWSER_SELECT_OPTION)
    assert str(menu_utils.open_file_browser_terminal(str(tmp_path))) == str(tmp_path / "music")


def test_a_folder_that_cant_be_listed_can_still_be_left(monkeypatch, tmp_path, capsys):
    real_listdir = menu_utils.os.listdir

    def listdir(path):
        if path == str(tmp_path):
            raise PermissionError(13, "Permission denied")
        return real_listdir(path)

    monkeypatch.setattr(menu_utils.os, "listdir", listdir)
    _answer(monkeypatch, menu_utils.FILE_BROWSER_EXIT_OPTION)
    assert menu_utils.open_file_browser_terminal(str(tmp_path)) is None
    assert "Permission denied" in capsys.readouterr().out
