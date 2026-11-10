import sys
import pytest
from pathlib import Path
from types import SimpleNamespace

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.youtube import import_from_playlist as m
from utils.common import sanitize
from utils.common.song_entry import SongEntry


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
    assert sanitize.strip_junk_brackets_anywhere("Song (Visualiser)") == "Song"
    assert sanitize.strip_junk_brackets_anywhere("Song (Clip officiel)") == "Song"
    assert sanitize.strip_junk_brackets_anywhere("Song (Officiel)") == "Song"
    assert sanitize.strip_junk_brackets_anywhere("Song (Wersja oryginał)") == "Song"
    assert sanitize.strip_junk_brackets_anywhere("Song (Lyric Video) - Artist") == "Song - Artist"
    assert sanitize.strip_junk_brackets_anywhere("Song (English Subtitles)") == "Song"
    assert sanitize.strip_junk_brackets_anywhere("Song (4K)") == "Song"
    assert sanitize.strip_junk_brackets_anywhere("Song (Remaster)") == "Song"
    assert sanitize.strip_junk_brackets_anywhere("Song (Remastered 2023)") == "Song"


def test_strip_junk_brackets_anywhere_keeps_non_junk_bracket():
    assert sanitize.strip_junk_brackets_anywhere("Sad Story (Out of Luck)") == "Sad Story (Out of Luck)"


# ---------------------------------------------------------
# build_metadata_from_item() - title cleanup cases
# ---------------------------------------------------------

def _build(title, channel="Some Channel"):
    return m.build_metadata_from_item({"channel": channel, "title": title, "video_id": "vid"})


def test_fullwidth_brackets_are_stripped_like_ascii_ones():
    metadata = _build("Fujii Kaze - Workin’ Hard（Official Video）")
    assert (metadata["artist_name"], metadata["title"]) == ("Fujii Kaze", "Workin’ Hard")
    assert sanitize.strip_junk_suffix("Song［HD］") == "Song"


def test_pipe_look_alike_suffix_is_stripped():
    metadata = _build("Dizzko Bizkit aka Mejsi - Bál ⎮ 2017")
    assert metadata["title"] == "Bál"
    assert sanitize.strip_pipe_suffix("Song ｜ Label") == "Song"


@pytest.mark.parametrize("title, artist, song", [
    # Real case: cut to "MIUOSH", which the channel fallback read as a title by "FDGREC"
    ("MIUOSH | SMOLIK | NOSPR - Wizje feat. Piotr Rogucki", "MIUOSH | SMOLIK | NOSPR", "Wizje"),
    ("NAVIBAND || VOLNY CHOR - Pad Dubam", "NAVIBAND || VOLNY CHOR", "Pad Dubam"),
    ("Hey ⎮ Afina - Karma", "Hey ⎮ Afina", "Karma"),
    # A " - " before the "|": what follows is a suffix, even with a dash of its own
    ("Hey - Karma | Label - Records", "Hey", "Karma"),
    ("kamanių šilelis – eiti | 1Mikrofonas", "kamanių šilelis", "eiti"),
    # No " - " at all: still cut, the channel is the artist
    ("Golan | Strong Faith", "FDGREC", "Golan"),
])
def test_pipe_before_the_only_dash_lists_the_artists(title, artist, song):
    metadata = _build(title, channel="FDGREC")
    assert (metadata["artist_name"], metadata["title"]) == (artist, song)


def test_pipes_listing_artists_are_only_for_a_title_still_to_be_split():
    # A stored or tagged title has no artists in it, so its "| ..." stays a suffix
    assert sanitize.sanitize_field("Song | Label - Records", sanitize.TITLE, sanitize.SOURCE_YOUTUBE) == "Song"


def test_full_song_bracket_is_stripped():
    assert _build("T-Series - Criminal (Full Song) Ra.One")["title"] == "Criminal Ra.One"


def test_file_name_title_is_turned_into_words():
    metadata = _build("Justyna Steczkowska - pejzaz_bez_ciebie.mpg")
    assert (metadata["artist_name"], metadata["title"]) == ("Justyna Steczkowska", "Pejzaz Bez Ciebie")


def test_clean_file_name_title_keeps_existing_capitalisation_and_plain_titles():
    assert sanitize.clean_file_name_title("Pejzaz_Bez_CIEBIE.mp4") == "Pejzaz Bez CIEBIE"
    assert sanitize.clean_file_name_title("Rock_n Roll") == "Rock_n Roll"
    assert sanitize.clean_file_name_title("Mr. Vain") == "Mr. Vain"


def test_corner_bracket_credit_splits_artist_from_title():
    metadata = _build("打首獄門同好会「なつのうた」", channel="GOKUMON(UchikubiGokumonDoukoukai)")
    assert (metadata["artist_name"], metadata["title"]) == ("打首獄門同好会", "なつのうた")


def test_corner_bracket_credit_ignores_junk_and_partial_brackets():
    assert m.split_corner_bracket_credit("Song「Lyric Video」") is None
    assert m.split_corner_bracket_credit("「なつのうた」") is None
    assert m.split_corner_bracket_credit("A「B」 C") is None


# ---------------------------------------------------------
# Channels titling uploads "Title - Artist" (TITLE_FIRST_CHANNELS)
# ---------------------------------------------------------

@pytest.mark.parametrize("title, artist, song_title, feat", [
    ("Lights Go Down - Zeds Dead & Jauz (FIFA 20 Volta Soundtrack)", "Zeds Dead & Jauz", "Lights Go Down", []),
    ("Electric Feel - MGMT (Flash FM - GTA VI Soundtrack)", "MGMT", "Electric Feel", []),
    ("We Got That Cool - Yves V (ft. Afrojack & Icona Pop) (FIFA 21 Official Volta Soundtrack)",
     "Yves V", "We Got That Cool", ["Afrojack", "Icona Pop"]),
    ("Like A Pimp - David Banner ft. Lil' Flip (Dirty South Classics -GTA VI Soundtrack)",
     "David Banner", "Like A Pimp", ["Lil' Flip"]),
    # the same channel's older uploads: "Artist- Title"
    ("Tom Misch- Good To Be Home (FIFA 19 Official Soundtrack)", "Tom Misch", "Good To Be Home", []),
])
def test_title_first_channel_reads_title_then_artist(title, artist, song_title, feat):
    metadata = _build(title, channel="favelamarek")
    assert (metadata["artist_name"], metadata["title"]) == (artist, song_title)
    assert metadata.get("feat_artists", []) == feat


def test_other_channels_keep_artist_then_title():
    metadata = _build("Lights Go Down - Zeds Dead & Jauz", channel="Some Channel")
    assert (metadata["artist_name"], metadata["title"]) == ("Lights Go Down", "Zeds Dead & Jauz")


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
    # Entries as playlist_item_metadata() gives them: filled in in place
    ok = SongEntry({"artist_name": "A", "title": "x"})
    filled = SongEntry({"artist_name": None, "title": "Mind Me", "_label": "Mind Me"})
    skipped = SongEntry({"artist_name": "", "title": "Slide", "_label": "Slide"})

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
    monkeypatch.setattr(m, "find_matching_artist", lambda name, session=None: _fake_artist("Genesis") if name == "Genesis" else None)

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


def test_a_swapped_entry_gets_the_artists_real_name_for_a_synonym(monkeypatch):
    # YT-22: synonyms were resolved for artists typed in, not for a swap - "akuteofficial" became an artist
    items = [{"channel": "X", "title": "Jakiś utwór - akuteofficial", "video_id": "1"}]
    monkeypatch.setattr(m, "get_playlist_items", lambda _: items)
    monkeypatch.setattr(m, "get_artists_from_db_session", lambda: [_fake_artist("Akute", synonyms="akuteofficial")])
    monkeypatch.setattr(m, "_fetch_video_description", lambda video_id: None)
    monkeypatch.setattr(m, "_review_missing_artists", lambda entries: ([], []))

    def swap_all(flagged):
        for metadata, _ in flagged:
            metadata["artist_name"], metadata["title"] = metadata["title"], metadata["artist_name"]
        return [metadata for metadata, _ in flagged], []
    monkeypatch.setattr(m, "_review_artist_title_swaps", swap_all)
    monkeypatch.setattr(m, "_find_possible_artist_title_swaps", lambda rest: [(md, None) for md in rest])
    late = []

    def fake_run_import_batch(metadata_list, late_entries=None):
        late.extend(late_entries()[0])
        return []
    monkeypatch.setattr(m, "run_import_batch", fake_run_import_batch)

    m.import_data_from_youtube_playlist("playlist")
    assert [(md["artist_name"], md["title"]) for md in late] == [("Akute", "Jakiś utwór")]

def test_unbracketed_trailing_junk_phrase_is_stripped():
    metadata = _build("“Golden” Official Lyric Video | KPop Demon Hunters | Sony Animation", channel="Sony Pictures Animation")
    assert metadata["title"] == "Golden"
    assert _build("Artist - Song Music Video (HD)")["title"] == "Song"


def test_unbracketed_junk_phrase_is_kept_when_it_is_the_whole_title():
    # The artist's own channel: the left side is the artist, not a title
    assert _build("Artist - Music Video", channel="Artist")["title"] == "Music Video"


@pytest.mark.parametrize("title", ["Glow - Lyric Video (English / French)", "Glow - Official Video",
                                   "Glow - Visualizer", "Glow - Official Music Video #dreampop"])
def test_a_junk_only_right_side_makes_the_left_the_title_and_the_channel_the_artist(title):
    metadata = _build(title, channel="Attic Ocean")
    assert (metadata["artist_name"], metadata["title"]) == ("Attic Ocean", "Glow")


@pytest.mark.parametrize("title, expected", [
    ("India.Arie - Video", ("India.Arie", "Video")),       # a song called "Video"
    ("Madonna - Music", ("Madonna", "Music")),
    ("Attic Ocean - Glow (Lyric Video)", ("Attic Ocean", "Glow")),
])
def test_a_real_title_on_the_right_is_kept(title, expected):
    metadata = _build(title, channel="Some Channel")
    assert (metadata["artist_name"], metadata["title"]) == expected


@pytest.mark.parametrize("title, channel, expected", [
    ("nipplepeople NIKADA", "nipplepeople", "NIKADA"),      # the channel's name, then only a space
    ("Siamés Summer Nights", "SIAMES", "Summer Nights"),
    ("nipplepeople", "nipplepeople", "nipplepeople"),       # never emptied
    ("Nipplepeoples NIKADA", "nipplepeople", "Nipplepeoples NIKADA"),  # a whole name only
])
def test_a_title_without_separator_loses_the_channel_name_it_starts_with(title, channel, expected):
    metadata = _build(title, channel=channel)
    assert (metadata["artist_name"], metadata["title"]) == (channel, expected)


def test_junk_only_title():
    assert m.is_junk_only_title("Lyric Video (English / French)")
    assert m.is_junk_only_title("Lyrics")
    assert not m.is_junk_only_title("Video")
    assert not m.is_junk_only_title("Glow (Lyric Video)")
    assert not m.is_junk_only_title("")


def test_unbracketed_junk_phrase_needs_a_whole_word_match():
    assert _build("Artist - Homemusicvideo")["title"] == "Homemusicvideo"


def test_trailing_quality_tag_is_stripped_after_other_junk():
    assert _build("MADUAR - Do It HD")["title"] == "Do It"
    assert _build("Artist - Do It HD (Official Video)")["title"] == "Do It"
    assert _build("Artist - Clean Town HQ")["title"] == "Clean Town"
    assert _build("Artist - Song 4K")["title"] == "Song"


def test_quality_tag_needs_a_whole_word():
    assert _build("Artist - Bad Song Shd")["title"] == "Bad Song Shd"
    assert _build("Artist - HD")["title"] == "HD"


def test_trailing_dash_year_is_stripped():
    metadata = _build("Hank Williams Jr. - Mr. Weatherman - 1982", channel="VHS-Goldie")
    assert (metadata["artist_name"], metadata["title"]) == ("Hank Williams Jr.", "Mr. Weatherman")


def test_dash_year_is_kept_when_it_is_the_whole_title_or_not_a_year():
    assert sanitize.strip_trailing_dash_year("1982") == "1982"
    assert _build("Artist - Song - 5000")["title"] == "Song - 5000"


def test_quoted_title_with_feat_bracket_takes_artists_from_the_credit():
    metadata = _build(
        "'City of Stars' (Duet ft. Ryan Gosling, Emma Stone) - La La Land Original Motion Picture Soundtrack",
        channel="Interscope Records",
    )
    assert (metadata["artist_name"], metadata["title"]) == ("Ryan Gosling, Emma Stone", "City of Stars")


def test_quoted_title_without_feat_bracket_is_split_as_usual():
    assert m.split_quoted_title_feat_credit("'Song' (Live) - Artist") is None
    assert _build("Artist - 'Song' (feat. Guest)")["artist_name"] == "Artist"


@pytest.mark.parametrize("title, artist, album, song_title", [
    ("Fisz Emade - Heavi Metal #09 - Disko", "Fisz Emade", "Heavi Metal", "Disko"),
    ("Fisz Emade - Heavi Metal #07 - Kawa i Papierosy", "Fisz Emade", "Heavi Metal", "Kawa i Papierosy"),
    ("Fisz Emade – Heavi Metal #09 – Disko", "Fisz Emade", "Heavi Metal", "Disko"),
])
def test_album_with_a_track_number_between_artist_and_title_is_the_album(title, artist, album, song_title):
    metadata = _build(title)
    assert (metadata["artist_name"], metadata["album"], metadata["title"]) == (artist, album, song_title)
    assert metadata.originals["album"] == album


@pytest.mark.parametrize("title, song_title", [
    ("James Gang - Funk #49", "Funk #49"),  # the number belongs to the title
    ("James Gang - Funk #49 - Live", "Funk #49 - Live"),  # its version, not a song
    ("Artist - Song #12 - Official Video", "Song #12"),  # an upload annotation
    ("Artist - Mixtape #5 - Song", "Mixtape #5 - Song"),  # only two digits count as a track number
    ("Shema - Hava Nagila", "Hava Nagila"),
    ("Led Zeppelin - Kashmir - Remaster", "Kashmir - Remaster"),
])
def test_title_without_an_album_track_number_gets_no_album(title, song_title):
    assert m.split_album_track_title(title) is None
    metadata = _build(title)
    assert (metadata["album"], metadata["title"]) == (None, song_title)


@pytest.mark.parametrize("title, artist, song_title, feat", [
    ("The Umbrella Academy Soundtrack | S01E04 | Mirage | TORO Y MOI | ", "Toro Y Moi", "Mirage", None),
    ("The Umbrella Academy Soundtrack | S01E05 | (Feels Like) Heaven | FICTION FACTORY | ",
     "Fiction Factory", "(Feels Like) Heaven", None),
    # The guest the title credits isn't a main artist too
    ("The Umbrella Academy Soundtrack | S01E05 | Happy Together (feat. Ray Toro) | GERARD WAY, RAY TORO | ",
     "Gerard Way", "Happy Together", ["Ray Toro"]),
    ("Stranger Things 3 Soundtrack | S03E01 | Never Surrender | Corey Hart", "Corey Hart", "Never Surrender", None),
])
def test_series_soundtrack_by_episode_reads_the_song_after_the_episode(title, artist, song_title, feat):
    # Real case, channel "Ugly Tuber": the "|" had been cut as a suffix, leaving
    # "Ugly Tuber - The Umbrella Academy Soundtrack" for every video
    metadata = m.build_metadata_from_item({"title": title, "channel": "Ugly Tuber", "video_id": "abc"})
    assert (metadata["artist_name"], metadata["title"], metadata["album"]) == (artist, song_title, None)
    assert metadata.get("feat_artists") == feat


@pytest.mark.parametrize("title", [
    "Song | Official Video",
    "The Umbrella Academy Soundtrack | Mirage | TORO Y MOI",  # no episode marker
    "Artist | S01E04 | Mirage | TORO Y MOI",  # no soundtrack the episode belongs to
])
def test_other_pipe_titles_are_no_series_soundtrack(title):
    assert m.split_episode_soundtrack_title(title) is None


def test_trailing_reupload_bracket_is_stripped():
    assert _build("DISCO BANIA - BRUNETKI I BLONDYNKI (reupload HD)")["title"] == "BRUNETKI I BLONDYNKI"
    assert _build("Artist - Song [Re-upload]")["title"] == "Song"


# ---------------------------------------------------------
# Deleted / private videos (YT-01)
# ---------------------------------------------------------

def test_ytdlp_playlist_skips_deleted_and_private_videos(monkeypatch):
    import json
    lines = [
        {"id": "aaaaaaaaaaa", "title": "Artist - Song", "channel": "Artist"},
        {"id": "bbbbbbbbbbb", "title": "[Private video]", "channel": None},
        {"id": "ccccccccccc", "title": "[Deleted video]", "channel": None},
    ]
    stdout = "\n".join(json.dumps(line) for line in lines)
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=stdout))

    items = m._fetch_playlist_items_ytdlp("PLx")

    assert [item["video_id"] for item in items] == ["aaaaaaaaaaa"]


def test_placeholder_titles_are_recognised_with_or_without_brackets():
    assert m.is_placeholder_title("Private video")
    assert m.is_placeholder_title("[Deleted video]")
    assert not m.is_placeholder_title("Private Video Blues")
    assert not m.is_placeholder_title(None)


@pytest.mark.parametrize("typed", [[None], ["", ""], ["Queen", None], ["  ", "Song"]])
def test_add_manually_with_nothing_typed_skips_the_entry(monkeypatch, typed):
    # YT-05: Ctrl+C (None) or Enter ("") in "Add manually" went into the import
    # as an artist / title of None or ""
    answers = iter(typed + [None] * 2)
    monkeypatch.setattr(m.questionary, "select", lambda *a, **k: SimpleNamespace(ask=lambda: m._SWAP_CHOICE_MANUAL))
    monkeypatch.setattr(m.questionary, "text", lambda *a, **k: SimpleNamespace(ask=lambda: next(answers)))
    metadata = {"artist_name": "Bohemian Rhapsody", "title": "Queen"}

    remaining, skipped = m._review_artist_title_swaps([(metadata, _fake_artist("Queen"))])

    assert remaining == []
    assert skipped == [("Bohemian Rhapsody - Queen", "Possible artist/title swap - nothing typed, not added")]


def test_add_manually_takes_what_was_typed(monkeypatch):
    answers = iter([" Queen ", "Bohemian Rhapsody"])
    monkeypatch.setattr(m.questionary, "select", lambda *a, **k: SimpleNamespace(ask=lambda: m._SWAP_CHOICE_MANUAL))
    monkeypatch.setattr(m.questionary, "text", lambda *a, **k: SimpleNamespace(ask=lambda: next(answers)))
    metadata = {"artist_name": "Bohemian Rhapsody", "title": "Queen"}

    remaining, skipped = m._review_artist_title_swaps([(metadata, _fake_artist("Queen"))])

    assert skipped == [] and remaining == [{"artist_name": "Queen", "title": "Bohemian Rhapsody"}]


def test_official_match_survives_an_unavailable_hit_in_the_search(monkeypatch):
    # YT-09: yt-dlp exits 1 when one hit is unavailable but still prints the
    # others; the exit code alone threw them away, so special channels lost
    # their official-match lookup
    import json
    hit = {"channel": "Eartha Kitt - Topic", "track": "Santa Baby", "artist": "Eartha Kitt"}
    result = SimpleNamespace(returncode=1, stdout=json.dumps(hit) + "\n",
                             stderr="ERROR: [youtube] abc: Video unavailable")
    monkeypatch.setattr(m, "_run_ytdlp_search", lambda query, max_results: result)
    logged = []
    monkeypatch.setattr(m, "_log_ytdlp_errors", lambda query, res: logged.append(query), raising=False)

    assert m._find_official_match_via_search("Eartha Kitt - Santa Baby (1953)") == ("Eartha Kitt", "Santa Baby")
    assert logged == ["Eartha Kitt - Santa Baby (1953)"]

    result.stdout = ""
    assert m._find_official_match_via_search("Eartha Kitt - Santa Baby (1953)") is None


def test_artist_in_the_title_is_found_across_letters_without_a_decomposition():
    # YT-32: "ł" / "ø" have no NFKD decomposition - the search folded them, the title clean-up didn't
    from utils.common.sanitize import strip_artist_from_title
    assert strip_artist_from_title('Łona "Rozmowy" (Official Video)', "Lona") == "Rozmowy (Official Video)"
    assert strip_artist_from_title("Ørganek - Głupi ja", "Organek") == "Głupi ja"
    assert strip_artist_from_title("Ołów", "Olowek") == "Ołów"  # whole words only, still
