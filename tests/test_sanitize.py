"""utils/common/sanitize.py: each source's steps, in order, do exactly what
that source's own cleanup does today (tests/test_name_cleanup_characterization.py
pins those outputs). The YouTube and mp3 imports, Clean names and song
requests run through it."""
import re

import pytest

from utils.common import sanitize as s
from utils.common.normalizer import composed, strip_format_characters
from utils.common.text_utils import strip_trailing_junk_suffix
from utils.discoveries.import_data_from_mp3_tags import _clean_fields
from utils.database.song_duplicates import split_feat_credits

from test_name_cleanup_characterization import MP3_CASES, REQUEST_FEAT_CASES, STORED_JUNK_CASES, YOUTUBE_CASES


# --- YouTube: the chain build_metadata_from_item() runs ---

def _youtube_raw_today(title):
    title = s.strip_pipe_suffix(title)
    title = s.strip_junk_brackets_anywhere(title)
    title = s.strip_hashtags(title)
    return s.MEDIA_EXTENSION_RE.sub("", title).strip()


def _youtube_title_today(title, artist):
    if title and artist:
        title = s.strip_artist_from_title(title, artist)
    if title:
        title = s.strip_junk_suffix(title)
        title = strip_trailing_junk_suffix(title) or title
        title = s.strip_trailing_dash_year(title)
        title = s.strip_junk_suffix(title)
        title = s.strip_quote_marks(title)
        title = s.clean_file_name_title(title)
    return title


YOUTUBE_RAW = [title for _channel, title, _expected in YOUTUBE_CASES]
# (artist, title as split off a video title)
YOUTUBE_FIELDS = [
    ("SIAMES", 'SIAMÉS "Summer Nights" [Official Animated Video]'),
    ("Ado", "【Ado】 unravel 歌いました"),
    ("Łona", "Lona Song"),
    ("Artist", "Song (Official Video) (HD)"),
    ("Artist", "Song (Official Video) Official Audio"),
    ("Artist", "Song Lyrics (HD) Official Video"),
    ("Artist", "Song Official Video (Lyrics) - 1999 (HD)"),
    ("Artist", "Song - 1999 (HD)"),
    ("Artist", "HD"),
    ("Artist", "(Official Video)"),
    ("Artist", '"Need You Tonight" (INXS cover)'),
    ("Artist", "pejzaz_bez_ciebie.mpg"),
    ("Artist", "Sierpień (2022)"),
    ("Artist", "Song (Remix)"),
    (None, "Mind Me (Official Audio)"),
    ("pejzaz_bez_ciebie", "Song"),
    ("Artist", ""),
]


@pytest.mark.parametrize("title", YOUTUBE_RAW)
def test_youtube_raw_steps_match_the_import(title):
    assert s.sanitize_raw(title, s.SOURCE_YOUTUBE) == _youtube_raw_today(title)


@pytest.mark.parametrize("artist, title", YOUTUBE_FIELDS)
def test_youtube_field_steps_match_the_import(artist, title):
    assert s.sanitize_field(title, s.TITLE, s.SOURCE_YOUTUBE, artist=artist) == _youtube_title_today(title, artist)
    expected_artist = s.clean_file_name_title(artist) if artist else artist
    assert s.sanitize_field(artist, s.ARTIST, s.SOURCE_YOUTUBE) == expected_artist


def test_youtube_metadata_cleans_the_title_with_the_artist_as_it_came():
    metadata = s.sanitize_metadata({"artist_name": "pejzaz_bez_ciebie", "title": "pejzaz_bez_ciebie Song",
                                    "album": None}, s.SOURCE_YOUTUBE)
    assert metadata == {"artist_name": "Pejzaz Bez Ciebie", "title": "Song", "album": None}


# --- mp3 tags: _clean_fields() ---

@pytest.mark.parametrize("tags, expected", MP3_CASES)
def test_mp3_steps_match_clean_fields(tags, expected):
    assert s.sanitize_metadata(dict(tags), s.SOURCE_MP3) == _clean_fields(dict(tags)) == expected


@pytest.mark.parametrize("stem", ["y2meta.com - Artist - Song", "y2meta.com", "Artist - Song-AzEBH6DZJVk-160k-1645418932557"])
def test_file_name_raw_step_is_strip_site_names(stem):
    from utils.common.normalizer import strip_site_names
    assert s.sanitize_raw(stem, s.SOURCE_FILE_NAME) == strip_site_names(stem)


# --- stored titles: Clean names' value-level steps ---

def _stored_today(value, field):
    # Clean names' value steps; since 2026-11-01 spaces go from both ends and
    # a title loses junk brackets anywhere and a year after a dash too
    value = value.replace("&amp;", "&")
    value = composed(value)
    value = strip_format_characters(value, keep_joiner=True)
    value = re.sub(r" {2,}", " ", value.strip())
    if field == s.TITLE:
        if any(s.YOUTUBE_TITLE_JUNK_MARKER_ANYWHERE_RE.search(m.group(1)) for m in s.ANY_BRACKET_RE.finditer(value)):
            value = s.strip_junk_brackets_anywhere(value)
        value = (s.strip_trailing_junk(value) or value) if value else value
        value = s.strip_trailing_dash_year(value) if value else value
    return value


STORED_VALUES = [title for title, _expected in STORED_JUNK_CASES] + [
    "  Song  (Official Video)", " \tSong", "\tSong", "Rock &amp; Roll", "Cz​ô​rny", "Abrásame", "👨‍👩‍👧 Family",
]


@pytest.mark.parametrize("value", STORED_VALUES)
@pytest.mark.parametrize("field", [s.TITLE, s.ARTIST, s.ALBUM])
def test_stored_steps_match_clean_names(value, field):
    assert s.sanitize_field(value, field, s.SOURCE_STORED) == _stored_today(value, field)


def test_review_step_is_only_proposed():
    # "Вjörk": a Cyrillic "В" in a Latin name
    assert s.sanitize_field("Вjörk", s.ARTIST, s.SOURCE_STORED) == "Вjörk"
    assert s.sanitize_changes("Вjörk", s.ARTIST, s.SOURCE_STORED) == [("lookalike_letters", "Вjörk", "Björk")]
    assert s.sanitize_changes("Вjörk", s.ARTIST, s.SOURCE_STORED, review=False) == []


def test_changes_name_each_step_that_changed_something():
    assert s.sanitize_changes("  Song &amp; Dance (Official Video)", s.TITLE, s.SOURCE_STORED) == [
        ("amp_entity", "  Song &amp; Dance (Official Video)", "  Song & Dance (Official Video)"),
        ("whitespace", "  Song & Dance (Official Video)", "Song & Dance (Official Video)"),
        ("junk_brackets_anywhere", "Song & Dance (Official Video)", "Song & Dance"),
    ]


# --- song requests: only the featured artists move ---

@pytest.mark.parametrize("title, expected", REQUEST_FEAT_CASES)
def test_request_metadata_splits_feat_credits(title, expected):
    metadata = s.sanitize_metadata({"artist_name": "Artist", "title": title, "album": None}, s.SOURCE_REQUEST)
    split_title, feat_artists = split_feat_credits(title)
    assert (metadata["title"], metadata["feat_artists"]) == (split_title or None, feat_artists) == expected
    assert metadata["artist_name"] == "Artist"


def test_only_requests_split_feat_credits():
    for source in (s.SOURCE_MP3, s.SOURCE_YOUTUBE, s.SOURCE_STORED, s.SOURCE_MANUAL):
        metadata = s.sanitize_metadata({"artist_name": "A", "title": "Song ft. B", "album": None}, source)
        assert metadata["title"] == "Song ft. B" and "feat_artists" not in metadata


# --- the registry itself ---

@pytest.mark.parametrize("value", ["  Song &amp; (Official Video) | x #y", "Вjörk", "y2meta.com"])
@pytest.mark.parametrize("field", [s.TITLE, s.ARTIST, s.ALBUM])
def test_typed_by_hand_is_left_alone(value, field):
    assert s.sanitize_field(value, field, s.SOURCE_MANUAL) == value
    assert s.sanitize_raw(value, s.SOURCE_MANUAL) == value


def test_empty_values_pass_through():
    for source in s.SOURCES:
        assert s.sanitize_field(None, s.TITLE, source) is None
        assert s.sanitize_field("", s.TITLE, source) == ""
        assert s.sanitize_raw(None, source) is None


def test_every_ordered_step_exists_and_every_step_is_ordered():
    for name in (*s.RAW_ORDER, *s.FIELD_ORDER):
        assert name in s.STEPS
    for name, step in s.STEPS.items():
        if s.STAGE_RAW in step.stages:
            assert name in s.RAW_ORDER
        if s.STAGE_FIELD in step.stages:
            assert name in s.FIELD_ORDER and step.fields
        assert step.sources <= set(s.SOURCES)


def test_a_step_listed_twice_runs_twice():
    names = [step.name for step in s.enabled_steps(s.SOURCE_YOUTUBE, s.STAGE_FIELD, s.TITLE)]
    assert names == ["amp_entity", "html_entities", "cp1250_mojibake", "nfc", "invisible_chars", "whitespace",
                     "site_names", "pipe_suffix", "junk_brackets_anywhere", "hashtags", "media_extension",
                     "artist_in_title", "junk_marker_brackets", "junk_phrases_any_bracket", "junk_phrases",
                     "trailing_dash_year", "junk_marker_brackets", "quote_marks", "file_name_artifacts"]


# --- Settings -> "Name cleanup" ---

from config.constants import SANITIZE_SOURCE_LABELS, SANITIZE_STEP_LABELS  # noqa: E402
from utils.common import sanitize_settings  # noqa: E402


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "sanitize_config.json"
    monkeypatch.setattr(sanitize_settings, "CONFIG_PATH", path)
    sanitize_settings.forget_cache()
    yield path
    sanitize_settings.forget_cache()


def test_a_step_switched_off_or_on_for_a_source(settings_file):
    tags = {"artist_name": "A &amp; B", "title": "Song (Official Video)", "album": None}
    s.set_step_sources("html_entities", set())
    s.set_step_sources("amp_entity", set())
    s.set_step_sources("junk_brackets_anywhere", set())
    s.set_step_sources("junk_marker_brackets", {s.SOURCE_MP3, s.SOURCE_YOUTUBE})
    assert s.sanitize_metadata(dict(tags), s.SOURCE_MP3) == {"artist_name": "A &amp; B", "title": "Song", "album": None}
    sanitize_settings.forget_cache()  # read back from the file
    assert not s.is_enabled("html_entities", s.SOURCE_MP3) and s.is_enabled("junk_marker_brackets", s.SOURCE_MP3)


def test_only_what_differs_from_the_defaults_is_stored(settings_file):
    import json
    s.set_step_sources("junk_marker_brackets", {s.SOURCE_YOUTUBE, s.SOURCE_MP3})
    s.set_step_sources("hashtags", s.default_sources("hashtags"))
    s.set_step_sources("quote_marks", s.default_sources("quote_marks") - {s.SOURCE_STORED})
    assert json.loads(settings_file.read_text()) == {"steps": {"junk_marker_brackets": {"mp3": True},
                                                               "quote_marks": {"stored": False}}}
    s.reset_step_sources()
    assert s.step_sources("junk_marker_brackets") == s.default_sources("junk_marker_brackets")


def test_unreadable_or_odd_settings_mean_the_defaults(settings_file):
    settings_file.write_text("{not json")
    assert s.step_sources("html_entities") == s.default_sources("html_entities")
    sanitize_settings.forget_cache()
    settings_file.write_text('{"steps": {"html_entities": {"mp3": "no", "manual": true}, "hashtags": 3}}')
    assert s.step_sources("html_entities") == s.default_sources("html_entities")
    # "manual" can't be turned on, whatever the file says
    sanitize_settings.forget_cache()
    settings_file.write_text('{"steps": {"html_entities": {"manual": true}}}')
    assert s.SOURCE_MANUAL not in s.step_sources("html_entities")


def test_which_sources_a_step_can_be_switched_for():
    for name in s.step_names():
        allowed = s.configurable_sources(name)
        assert s.default_sources(name) <= set(allowed)
        assert s.SOURCE_MANUAL not in allowed
        assert name in SANITIZE_STEP_LABELS
    assert s.configurable_sources("lookalike_letters") == (s.SOURCE_STORED,)
    assert s.configurable_sources("youtube_spaces") == (s.SOURCE_FILE_NAME, s.SOURCE_YOUTUBE)
    # Steps known to break real names stay where they were written for
    for name in ("artist_in_title", "junk_marker_brackets", "file_name_artifacts"):
        assert s.default_sources(name) == {s.SOURCE_YOUTUBE}
    assert s.SOURCE_STORED not in s.default_sources("edge_separators")
    assert s.SOURCE_STORED not in s.configurable_sources("artist_in_title")  # Clean names has no entry artist
    assert s.configurable_sources(s.FEAT_TO_CREDITS) == (s.SOURCE_REQUEST,)
    assert set(SANITIZE_SOURCE_LABELS) == set(s.SOURCES) - {s.SOURCE_MANUAL}


def test_clean_names_runs_every_step_turned_on_for_the_database(settings_file):
    from utils.database import name_cleanup
    everything_after = ["invisible_chars", "whitespace", "site_names", "pipe_suffix", "junk_brackets_anywhere",
                        "hashtags", "media_extension", "junk_phrases", "trailing_dash_year", "quote_marks"]
    assert name_cleanup.stored_step_passes() == (["amp_entity", "html_entities", "cp1250_mojibake"], everything_after)
    # Never the ones that break stored names, unless turned on
    s.set_step_sources("junk_marker_brackets", {s.SOURCE_YOUTUBE, s.SOURCE_STORED})
    s.set_step_sources("trailing_dash_year", {s.SOURCE_YOUTUBE})
    assert name_cleanup.stored_step_passes()[1] == [
        "invisible_chars", "whitespace", "site_names", "pipe_suffix", "junk_brackets_anywhere", "hashtags",
        "media_extension", "junk_marker_brackets", "junk_phrases", "junk_marker_brackets", "quote_marks"]


def test_a_stored_name_is_never_cleaned_to_nothing():
    from types import SimpleNamespace
    from utils.database import name_cleanup
    song = SimpleNamespace(title="y2meta.com", album="Album (greenmp3.com)", artist=SimpleNamespace(id=1, name="A"))
    changes = name_cleanup.apply_step([song], "site_names")
    assert song.title == "y2meta.com" and song.album == "Album"
    assert [c.fields for c in changes] == [["album: 'Album'"]]


def test_the_menu_offers_the_database_too(settings_file, monkeypatch):
    import menu.main_menu  # noqa: F401 - imported first, as main.py does
    from menu.main_menu.settings import name_cleanup as menu

    class _Answer:
        def __init__(self, value):
            self.value = value

        def ask(self):
            return self.value

    monkeypatch.setattr(menu.questionary, "checkbox", lambda *a, **kw: _Answer([s.SOURCE_YOUTUBE, s.SOURCE_MP3]))
    asked = []
    monkeypatch.setattr(menu, "ask_yes_no", lambda q: asked.append(q) or True)
    menu._change_step("junk_marker_brackets")
    assert asked and s.step_sources("junk_marker_brackets") == {s.SOURCE_YOUTUBE, s.SOURCE_MP3, s.SOURCE_STORED}

    # Turned off only: nothing to ask
    asked.clear()
    monkeypatch.setattr(menu.questionary, "checkbox", lambda *a, **kw: _Answer([]))
    menu._change_step("junk_marker_brackets")
    assert not asked and s.step_sources("junk_marker_brackets") == frozenset()


# --- the shared regex pieces (utils/common/title_patterns.py) ---

def test_shared_title_patterns():
    from utils.common import title_patterns as tp
    feat = re.compile(r"^" + tp.FEAT_WORD + r"\s", re.IGNORECASE)
    assert all(feat.match(w + " B") for w in ("feat", "feat.", "Ft", "ft.", "featuring"))
    assert not any(feat.match(w + " B") for w in ("feather", "with", "fe."))
    assert re.match(tp.FEAT_OR_WITH_WORD + r"\s", "with B")
    youtube = re.compile(tp.YOUTUBE_BRACKETED)
    for opening, closing in tp.YOUTUBE_BRACKET_PAIRS.items():
        assert youtube.fullmatch(f"{opening}HD{closing}").group(1) == "HD"
    assert not youtube.fullmatch("(a (b) c)") and not youtube.fullmatch("《HD》")
    assert re.findall(tp.BRACKETED, "Song (Live) [2020] 【x】") == ["Live", "2020"]
    assert re.fullmatch(tp.YEAR, "1999") and not re.fullmatch(tp.YEAR, "2100")
