import os
import sys
from types import SimpleNamespace

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from utils.ui import songless_artist_review as review


def _artist(artist_id, name):
    return SimpleNamespace(id=artist_id, name=name, albums=[])


def test_a_look_alike_can_be_deleted_as_a_misspelling(monkeypatch):
    # "egenda -> merge into Legenda" was the only way to remove a misspelt
    # leftover, which kept "egenda" as a synonym of Legenda
    egenda, legenda, style = _artist(1, "egenda"), _artist(2, "Legenda"), _artist(3, "Style")
    monkeypatch.setattr(review, "open_and_set_global_database_sessions", lambda: (object(), None))
    monkeypatch.setattr(review, "submit_global_database_session", lambda: None)
    monkeypatch.setattr(review, "find_songless_artists", lambda session: [(egenda, legenda), (style, None)])
    monkeypatch.setattr(review, "ask_yes_no", lambda *a, **k: False)
    shown = {}

    def tick(question, rows, label, checked, rule, facts):
        shown["labels"] = [label(row) for row in rows]
        shown["checked"] = [checked(row) for row in rows]
        return [rows[1], rows[2]]  # egenda's delete, Style

    removed = []
    monkeypatch.setattr(review, "tick_items", tick)
    monkeypatch.setattr(review, "remove_songless_artist", lambda session, artist, heir: removed.append((artist, heir)))

    assert review.review_songless_artists() == 2
    assert shown["labels"] == [
        'egenda  ->  merge into "Legenda" (name kept as its synonym)',
        'egenda  ->  delete (a misspelling of "Legenda": the name not kept)',
        "Style  ->  delete",
    ]
    assert shown["checked"] == [False, False, True]
    assert removed == [(egenda, None), (style, None)]


def test_a_look_alike_ticked_both_ways_is_deleted_once():
    egenda, legenda = _artist(1, "egenda"), _artist(2, "Legenda")
    pan, span = _artist(3, "Pan"), _artist(4, "Span")

    actions = review._picked_actions([(egenda, legenda, True), (egenda, legenda, False), (pan, span, True)])

    assert actions == [(egenda, None), (pan, span)]


def test_the_delete_row_tells_rules_it_is_a_look_alike():
    egenda, legenda = _artist(1, "egenda"), _artist(2, "Legenda")

    assert review._facts((egenda, legenda, False)) == {
        "merge": False, "look_alike": True, "name_similarity": review._facts((egenda, legenda, True))["name_similarity"],
        "empty_albums": 0}
