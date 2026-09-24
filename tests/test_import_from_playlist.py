import sys
from pathlib import Path
from types import SimpleNamespace

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.youtube import import_from_playlist as m


def _fake_artist(name, synonyms=None):
    return SimpleNamespace(name=name, synonyms=synonyms)


# ---------------------------------------------------------
# _resolve_artist_synonyms()
# ---------------------------------------------------------

def test_resolve_artist_synonyms_swaps_channel_handle_for_canonical_name(monkeypatch):
    monkeypatch.setattr(
        m, "get_artists_from_db_session", lambda: [_fake_artist("Akute", synonyms="akuteofficial")]
    )
    metadata_list = [{"artist_name": "akuteofficial", "title": "jakistamutwor"}]

    m._resolve_artist_synonyms(metadata_list)

    assert metadata_list[0]["artist_name"] == "Akute"


def test_resolve_artist_synonyms_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(
        m, "get_artists_from_db_session", lambda: [_fake_artist("Akute", synonyms="AkuteOfficial")]
    )
    metadata_list = [{"artist_name": "AKUTEOFFICIAL", "title": "song"}]

    m._resolve_artist_synonyms(metadata_list)

    assert metadata_list[0]["artist_name"] == "Akute"


def test_resolve_artist_synonyms_leaves_non_matching_artist_untouched(monkeypatch):
    monkeypatch.setattr(
        m, "get_artists_from_db_session", lambda: [_fake_artist("Akute", synonyms="akuteofficial")]
    )
    metadata_list = [{"artist_name": "Some Other Artist", "title": "song"}]

    m._resolve_artist_synonyms(metadata_list)

    assert metadata_list[0]["artist_name"] == "Some Other Artist"


def test_resolve_artist_synonyms_handles_multiple_synonyms_per_artist(monkeypatch):
    monkeypatch.setattr(
        m,
        "get_artists_from_db_session",
        lambda: [_fake_artist("Бумбокс", synonyms="familyboombox, Boombox")],
    )
    metadata_list = [
        {"artist_name": "familyboombox", "title": "a"},
        {"artist_name": "Boombox", "title": "b"},
    ]

    m._resolve_artist_synonyms(metadata_list)

    assert metadata_list[0]["artist_name"] == "Бумбокс"
    assert metadata_list[1]["artist_name"] == "Бумбокс"


def test_resolve_artist_synonyms_skips_entries_without_artist_name(monkeypatch):
    monkeypatch.setattr(
        m, "get_artists_from_db_session", lambda: [_fake_artist("Akute", synonyms="akuteofficial")]
    )
    metadata_list = [{"artist_name": None, "title": "song"}]

    m._resolve_artist_synonyms(metadata_list)

    assert metadata_list[0]["artist_name"] is None


def test_resolve_artist_synonyms_no_op_when_no_artists_have_synonyms(monkeypatch):
    monkeypatch.setattr(
        m, "get_artists_from_db_session", lambda: [_fake_artist("Akute", synonyms=None)]
    )
    metadata_list = [{"artist_name": "akuteofficial", "title": "song"}]

    m._resolve_artist_synonyms(metadata_list)

    assert metadata_list[0]["artist_name"] == "akuteofficial"
