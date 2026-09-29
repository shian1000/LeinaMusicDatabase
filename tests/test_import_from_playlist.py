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


# ---------------------------------------------------------
# _parse_quoted_title_by_artist()
# ---------------------------------------------------------

def test_parse_quoted_title_by_artist_extracts_credit_after_episode_label():
    title = 'Umbrella Academy Season 4 Episode 1 - OST: "Santa Baby" by Eartha Kitt'

    assert m._parse_quoted_title_by_artist(title) == ("Eartha Kitt", "Santa Baby")


def test_parse_quoted_title_by_artist_handles_curly_quotes():
    title = 'Some Show - OST: “Santa Baby” by Eartha Kitt'

    assert m._parse_quoted_title_by_artist(title) == ("Eartha Kitt", "Santa Baby")


def test_parse_quoted_title_by_artist_returns_none_without_quoted_credit():
    assert m._parse_quoted_title_by_artist("Eartha Kitt - Santa Baby") is None


def test_parse_quoted_title_by_artist_returns_none_for_empty_title():
    assert m._parse_quoted_title_by_artist("") is None


def test_parse_quoted_title_by_artist_trims_trailing_album_credit():
    title = '"Cyberwildlife Park" by Marcin Przybylowicz - Cyberpunk: Edgerunners [OST]'

    assert m._parse_quoted_title_by_artist(title) == ("Marcin Przybylowicz", "Cyberwildlife Park")


def test_parse_quoted_title_by_artist_falls_back_to_unquoted_dash_credit():
    title = "CYBERPUNK 2077 - KILL THE MESSENGER by Rezodrone (Jason Charles Miller & Jamison Boaz)"

    assert m._parse_quoted_title_by_artist(title) == (
        "Rezodrone (Jason Charles Miller & Jamison Boaz)",
        "KILL THE MESSENGER",
    )


# ---------------------------------------------------------
# resolve_special_channel_metadata()
# ---------------------------------------------------------

def test_resolve_special_channel_metadata_prefers_search_match(monkeypatch):
    monkeypatch.setattr(m, "_find_official_match_via_search", lambda title, **_: ("Eartha Kitt", "Santa Baby"))
    monkeypatch.setattr(m, "_parse_quoted_title_by_artist", lambda title: ("Wrong Artist", "Wrong Title"))
    monkeypatch.setattr(m, "_fetch_video_description", lambda video_id: "should not be called")

    item = {"channel": "aesthetic waves", "title": '... "Santa Baby" by Eartha Kitt', "video_id": "abc"}

    assert m.resolve_special_channel_metadata(item) == ("Eartha Kitt", "Santa Baby")


def test_resolve_special_channel_metadata_falls_back_to_quoted_title_credit(monkeypatch):
    monkeypatch.setattr(m, "_find_official_match_via_search", lambda title, **_: None)
    monkeypatch.setattr(m, "_fetch_video_description", lambda video_id: "should not be called")

    item = {
        "channel": "aesthetic waves",
        "title": 'Umbrella Academy Season 4 Episode 1 - OST: "Santa Baby" by Eartha Kitt',
        "video_id": "abc",
    }

    assert m.resolve_special_channel_metadata(item) == ("Eartha Kitt", "Santa Baby")


def test_resolve_special_channel_metadata_falls_back_to_description(monkeypatch):
    monkeypatch.setattr(m, "_find_official_match_via_search", lambda title, **_: None)
    monkeypatch.setattr(m, "_parse_quoted_title_by_artist", lambda title: None)
    monkeypatch.setattr(m, "_fetch_video_description", lambda video_id: "Music : Care by Ezra Furman")

    item = {"channel": "soundtrack series", "title": "S02E08", "video_id": "abc"}

    assert m.resolve_special_channel_metadata(item) == ("Ezra Furman", "Care")


def test_resolve_special_channel_metadata_falls_back_to_quoted_title_credit_with_album_suffix(monkeypatch):
    monkeypatch.setattr(m, "_find_official_match_via_search", lambda title, **_: None)
    monkeypatch.setattr(m, "_fetch_video_description", lambda video_id: "should not be called")

    item = {
        "channel": "soundtracksost",
        "title": '"Cyberwildlife Park" by Marcin Przybylowicz - Cyberpunk: Edgerunners [OST]',
        "video_id": "abc",
    }

    assert m.resolve_special_channel_metadata(item) == ("Marcin Przybylowicz", "Cyberwildlife Park")


def test_resolve_special_channel_metadata_falls_back_to_unquoted_dash_credit(monkeypatch):
    monkeypatch.setattr(m, "_find_official_match_via_search", lambda title, **_: None)
    monkeypatch.setattr(m, "_fetch_video_description", lambda video_id: "should not be called")

    item = {
        "channel": "lakeshore records",
        "title": "CYBERPUNK 2077 - KILL THE MESSENGER by Rezodrone (Jason Charles Miller & Jamison Boaz)",
        "video_id": "abc",
    }

    assert m.resolve_special_channel_metadata(item) == (
        "Rezodrone (Jason Charles Miller & Jamison Boaz)",
        "KILL THE MESSENGER",
    )


def test_resolve_special_channel_metadata_returns_none_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(m, "_find_official_match_via_search", lambda title, **_: None)
    monkeypatch.setattr(m, "_parse_quoted_title_by_artist", lambda title: None)
    monkeypatch.setattr(m, "_fetch_video_description", lambda video_id: None)

    item = {"channel": "aesthetic waves", "title": "no credit here", "video_id": "abc"}

    assert m.resolve_special_channel_metadata(item) is None


# ---------------------------------------------------------
# strip_junk_brackets_anywhere()
# ---------------------------------------------------------

def test_strip_junk_brackets_anywhere_strips_new_marker_words():
    assert m.strip_junk_brackets_anywhere("Song (Visualiser)") == "Song"
    assert m.strip_junk_brackets_anywhere("Song (Clip officiel)") == "Song"
    assert m.strip_junk_brackets_anywhere("Song (Officiel)") == "Song"
    assert m.strip_junk_brackets_anywhere("Song (Wersja oryginał)") == "Song"
    assert m.strip_junk_brackets_anywhere("Song (Lyric Video) - Artist") == "Song - Artist"
    assert m.strip_junk_brackets_anywhere("Song (English Subtitles)") == "Song"
    assert m.strip_junk_brackets_anywhere("Song (4K)") == "Song"
    assert m.strip_junk_brackets_anywhere("Song (Remaster)") == "Song"
    assert m.strip_junk_brackets_anywhere("Song (Remastered 2023)") == "Song"


def test_strip_junk_brackets_anywhere_keeps_non_junk_bracket():
    assert m.strip_junk_brackets_anywhere("Sad Story (Out of Luck)") == "Sad Story (Out of Luck)"
