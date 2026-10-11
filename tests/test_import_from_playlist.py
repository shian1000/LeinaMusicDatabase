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


# ---------------------------------------------------------
# build_metadata_from_item() - title cleanup cases
# ---------------------------------------------------------

def _build(title, channel="Some Channel"):
    return m.build_metadata_from_item({"channel": channel, "title": title, "video_id": "vid"})


def test_fullwidth_brackets_are_stripped_like_ascii_ones():
    metadata = _build("Fujii Kaze - Workin’ Hard（Official Video）")
    assert (metadata["artist_name"], metadata["title"]) == ("Fujii Kaze", "Workin’ Hard")
    assert m.strip_junk_suffix("Song［HD］") == "Song"


def test_pipe_look_alike_suffix_is_stripped():
    metadata = _build("Dizzko Bizkit aka Mejsi - Bál ⎮ 2017")
    assert metadata["title"] == "Bál"
    assert m.strip_pipe_suffix("Song ｜ Label") == "Song"


def test_full_song_bracket_is_stripped():
    assert _build("T-Series - Criminal (Full Song) Ra.One")["title"] == "Criminal Ra.One"


def test_file_name_title_is_turned_into_words():
    metadata = _build("Justyna Steczkowska - pejzaz_bez_ciebie.mpg")
    assert (metadata["artist_name"], metadata["title"]) == ("Justyna Steczkowska", "Pejzaz Bez Ciebie")


def test_clean_file_name_title_keeps_existing_capitalisation_and_plain_titles():
    assert m.clean_file_name_title("Pejzaz_Bez_CIEBIE.mp4") == "Pejzaz Bez CIEBIE"
    assert m.clean_file_name_title("Rock_n Roll") == "Rock_n Roll"
    assert m.clean_file_name_title("Mr. Vain") == "Mr. Vain"


def test_corner_bracket_credit_splits_artist_from_title():
    metadata = _build("打首獄門同好会「なつのうた」", channel="GOKUMON(UchikubiGokumonDoukoukai)")
    assert (metadata["artist_name"], metadata["title"]) == ("打首獄門同好会", "なつのうた")


def test_corner_bracket_credit_ignores_junk_and_partial_brackets():
    assert m.split_corner_bracket_credit("Song「Lyric Video」") is None
    assert m.split_corner_bracket_credit("「なつのうた」") is None
    assert m.split_corner_bracket_credit("A「B」 C") is None


# ---------------------------------------------------------
# Generic Topic channels ("Release - Topic")
# ---------------------------------------------------------

_TOPIC_DESCRIPTION = """Provided to YouTube by Ditto Music

Mind Me · Oordaya

Mind Me

Released on: 2018-10-29

Auto-generated by YouTube."""


def test_generic_topic_channel_leaves_artist_empty():
    metadata = _build("Mind Me", channel="Release - Topic")
    assert metadata["artist_name"] is None
    assert metadata["title"] == "Mind Me"


def test_regular_topic_channel_still_uses_channel_name():
    assert _build("Mind Me", channel="Oordaya - Topic")["artist_name"] == "Oordaya"


def test_parse_topic_description_artist_reads_credit_line():
    assert m._parse_topic_description_artist(_TOPIC_DESCRIPTION) == "Oordaya"
    assert m._parse_topic_description_artist("x\n\nSong · A · B\n") == "A, B"


def test_parse_topic_description_artist_falls_back_to_main_artist_line():
    assert m._parse_topic_description_artist("Some text\nMain Artist: Frankie Cosmos\n") == "Frankie Cosmos"
    assert m._parse_topic_description_artist("nothing useful") is None


def test_resolve_generic_topic_artist_uses_description(monkeypatch):
    monkeypatch.setattr(m, "_fetch_video_description", lambda video_id: _TOPIC_DESCRIPTION)
    assert m.resolve_generic_topic_artist({"channel": "Release - Topic", "title": "Mind Me", "video_id": "v"}) == "Oordaya"


def test_resolve_generic_topic_artist_returns_none_without_description(monkeypatch):
    monkeypatch.setattr(m, "_fetch_video_description", lambda video_id: None)
    assert m.resolve_generic_topic_artist({"channel": "Release - Topic", "title": "Mind Me", "video_id": "v"}) is None


# ---------------------------------------------------------
# _review_missing_artists()
# ---------------------------------------------------------

def _fake_text_answers(monkeypatch, answers):
    answers = iter(answers)
    monkeypatch.setattr(m.questionary, "text", lambda *a, **k: SimpleNamespace(ask=lambda: next(answers)))


def test_review_missing_artists_fills_in_or_skips(monkeypatch):
    _fake_text_answers(monkeypatch, ["Oordaya", ""])
    ok = {"artist_name": "A", "title": "x"}
    filled = {"artist_name": None, "title": "Mind Me", "_label": "Mind Me"}
    skipped = {"artist_name": "", "title": "Slide", "_label": "Slide"}

    remaining, pre_skipped = m._review_missing_artists([ok, filled, skipped])

    assert remaining == [ok, filled]
    assert filled["artist_name"] == "Oordaya"
    assert [label for label, _ in pre_skipped] == ["Slide"]


def test_review_missing_artists_no_op_when_every_entry_has_artist(monkeypatch):
    monkeypatch.setattr(m.questionary, "text", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    entries = [{"artist_name": "A", "title": "x"}]
    assert m._review_missing_artists(entries) == (entries, [])


# ---------------------------------------------------------
# import_data_from_youtube_playlist() - questions come after the slow import
# ---------------------------------------------------------

def test_held_back_entries_are_only_asked_about_after_the_main_batch(monkeypatch):
    items = [
        {"channel": "X", "title": "Genesis Fan - Song", "video_id": "1"},
        {"channel": "X", "title": "Am I Very Wrong - Genesis", "video_id": "2"},
        {"channel": "Release - Topic", "title": "Mind Me", "video_id": "3"},
    ]
    monkeypatch.setattr(m, "get_playlist_items", lambda _: items)
    monkeypatch.setattr(m, "get_artists_from_db_session", lambda: [])
    monkeypatch.setattr(m, "_fetch_video_description", lambda video_id: None)
    monkeypatch.setattr(m, "find_matching_artist", lambda name: _fake_artist("Genesis") if name == "Genesis" else None)

    events = []
    monkeypatch.setattr(m, "_review_missing_artists", lambda entries: (events.append(("missing", entries)) or ([], [])))
    monkeypatch.setattr(m, "_review_artist_title_swaps", lambda flagged: (events.append(("swaps", flagged)) or ([], [])))

    def fake_run_import_batch(metadata_list, late_entries=None):
        events.append(("main", [md["title"] for md in metadata_list]))
        late_entries()
        return []

    monkeypatch.setattr(m, "run_import_batch", fake_run_import_batch)

    m.import_data_from_youtube_playlist("playlist")

    assert [name for name, _ in events] == ["main", "missing", "swaps"]
    assert events[0][1] == ["Song"]
    assert [md["title"] for md in events[1][1]] == ["Mind Me"]
    assert [md["title"] for md, _ in events[2][1]] == ["Genesis"]
