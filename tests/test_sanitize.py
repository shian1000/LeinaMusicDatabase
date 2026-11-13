"""utils/common/sanitize.py: each source's steps, in order, do exactly what
that source's own cleanup does today (tests/test_name_cleanup_characterization.py
pins those outputs). The YouTube and mp3 imports, Clean names and song
requests run through it."""
import re

import pytest

from utils.common import sanitize as s
from utils.common.normalizer import composed, strip_format_characters
from utils.common.sanitize.text import strip_producer_credit, strip_trailing_junk_suffix
from utils.discoveries.import_data_from_mp3_tags import _clean_fields
from utils.common.artist_credits import split_feat_credits

from test_name_cleanup_characterization import MP3_CASES, REQUEST_FEAT_CASES, STORED_JUNK_CASES, YOUTUBE_CASES
from utils.common.song_entry import SongEntry


# --- YouTube: build_metadata_from_item()'s results are pinned in
# test_name_cleanup_characterization.py (the steps' order since 2026-11-01:
# characters, what the title says, removing, tidying) ---

def test_youtube_metadata_cleans_the_title_with_the_artist_as_it_came():
    metadata = s.sanitize_metadata({"artist_name": "pejzaz_bez_ciebie", "title": "pejzaz_bez_ciebie - Song",
                                    "album": None}, s.SOURCE_YOUTUBE)
    assert metadata == {"artist_name": "Pejzaz Bez Ciebie", "title": "Song", "album": None}


# --- mp3 tags: _clean_fields() ---

@pytest.mark.parametrize("tags, expected", MP3_CASES)
def test_mp3_steps_match_clean_fields(tags, expected):
    assert s.sanitize_metadata(dict(tags), s.SOURCE_MP3) == _clean_fields(dict(tags)) == expected


@pytest.mark.parametrize("stem", ["y2meta.com - Artist - Song", "y2meta.com", "Artist - Song-AzEBH6DZJVk-160k-1645418932557"])
def test_file_name_raw_step_is_strip_site_names(stem):
    from utils.common.sanitize.text import strip_site_names
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
        ("html_entities", "  Song &amp; Dance (Official Video)", "  Song & Dance (Official Video)"),
        ("whitespace", "  Song & Dance (Official Video)", "Song & Dance (Official Video)"),
        ("junk_brackets_anywhere", "Song & Dance (Official Video)", "Song & Dance"),
    ]


# --- song requests: only the featured artists move ---

@pytest.mark.parametrize("title, expected", REQUEST_FEAT_CASES)
def test_request_metadata_splits_feat_credits(title, expected):
    metadata = s.sanitize_metadata({"artist_name": "Artist", "title": title, "album": None}, s.SOURCE_REQUEST)
    split_title, feat_artists = split_feat_credits(title)
    assert (metadata["title"], metadata.get("feat_artists", [])) == (split_title or None, feat_artists) == expected
    assert metadata["artist_name"] == "Artist"


def test_the_imports_split_feat_credits_and_a_typed_name_keeps_them():
    # Since 2026-11-01 the mp3 and YouTube imports too, not only requests
    for source in (s.SOURCE_MP3, s.SOURCE_YOUTUBE, s.SOURCE_REQUEST):
        metadata = s.sanitize_metadata({"artist_name": "A", "title": "Song ft. B", "album": None}, source)
        assert (metadata["title"], metadata["feat_artists"]) == ("Song", ["B"])
    metadata = s.sanitize_metadata({"artist_name": "A", "title": "Song ft. B", "album": None}, s.SOURCE_MANUAL)
    assert metadata["title"] == "Song ft. B" and "feat_artists" not in metadata
    # Cleaned twice (tags, then the file name): the names found first stay
    twice = s.sanitize_metadata({"artist_name": "A", "title": "Song ft. B", "feat_artists": ["C"]}, s.SOURCE_MP3)
    assert twice["feat_artists"] == ["C", "B"]


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


def test_a_step_listed_twice_runs_twice():
    # The agreed order: characters, what the title says, removing, tidying
    names = [step.name for step in s.enabled_steps(s.SOURCE_YOUTUBE, s.STAGE_FIELD, s.TITLE)]
    assert names == ["html_entities", "cp1250_mojibake", "nfc", "media_extension", "file_name_artifacts",
                     "invisible_chars", "whitespace", "lookalike_letters",
                     "project_name", "cover_credit", "feat_to_credits", "title_year", "artist_in_title",
                     "site_names", "pipe_suffix", "junk_brackets_anywhere", "producer_credit", "hashtags",
                     "junk_marker_brackets", "junk_phrases_any_bracket", "junk_phrases", "title_year",
                     "junk_marker_brackets", "quote_marks", "whitespace", "edge_separators"]


# --- Settings -> "Name cleanup": one switch per meaning, for every source ---

from config.constants import SANITIZE_SWITCH_LABELS  # noqa: E402
from utils.common.sanitize import settings as sanitize_settings  # noqa: E402


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "sanitize_config.json"
    monkeypatch.setattr(sanitize_settings, "CONFIG_PATH", path)
    sanitize_settings.forget_cache()
    yield path
    sanitize_settings.forget_cache()


def test_every_step_has_one_switch_shown_in_order():
    assert list(SANITIZE_SWITCH_LABELS) == list(s.SWITCHES)
    assert {step.switch for step in s.STEPS.values()} == set(s.SWITCHES)
    # The menu's order is the run's order: a switch's first step comes after the one before's
    # (the spaces are tidied at both ends, shown last)
    first = [min(s.FIELD_ORDER.index(n) for n in s.switch_steps(sw) if n in s.FIELD_ORDER)
             for sw in s.SWITCHES if sw != "spaces"]
    assert first == sorted(first) and s.SWITCHES[-1] == "spaces"


def test_a_switch_is_the_same_for_every_source(settings_file):
    assert all(s.switch_on(switch) for switch in s.SWITCHES)  # every switch on by default
    s.set_switch_on("junk_in_titles", False)
    sanitize_settings.forget_cache()  # read back from the file
    for source in s.FIELD_SOURCES:
        assert not s.is_enabled("junk_brackets_anywhere", source) and not s.is_enabled("junk_phrases", source)
    for source in s.SOURCES:
        assert s.is_enabled("whitespace", source) == (source in s.FIELD_SOURCES)
    assert s.sanitize_field("Song (Visualiser)", s.TITLE, s.SOURCE_MP3) == "Song (Visualiser)"
    assert s.sanitize_field("Song (1991)", s.TITLE, s.SOURCE_MP3) == "Song"  # the year is its own switch


def test_where_a_step_runs_follows_from_what_it_works_on():
    assert s.runs_for("pipe_suffix") == s.RAW_SOURCES | s.FIELD_SOURCES
    assert s.runs_for("whitespace") == s.FIELD_SOURCES
    assert s.runs_for(s.FEAT_TO_CREDITS) == s.FIELD_SOURCES
    for name in s.STEPS:
        assert s.SOURCE_MANUAL not in s.runs_for(name)


def test_only_what_differs_from_the_defaults_is_stored(settings_file):
    import json
    s.set_switch_on("quote_marks", False)
    s.set_switch_on("spaces", True)
    assert json.loads(settings_file.read_text()) == {"steps": {"quote_marks": False}}
    s.reset_switches()
    assert s.switch_on("quote_marks")


def test_unreadable_odd_or_older_settings(settings_file):
    settings_file.write_text("{not json")
    assert s.switch_on("html_entities")
    sanitize_settings.forget_cache()
    settings_file.write_text('{"steps": {"html_entities": "no", "spaces": 3}}')
    assert s.switch_on("html_entities") and s.switch_on("spaces")
    # Files of older versions, which switched each step (and once per source):
    # a switch is off when one of its steps was switched off
    sanitize_settings.forget_cache()
    settings_file.write_text('{"steps": {"hashtags": false, "amp_entity": {"stored": false}, "nfc": true,'
                             ' "trailing_dash_year": {"mp3": true}}}')
    assert not s.switch_on("junk_in_titles") and not s.switch_on("html_entities")
    assert s.switch_on("encoding") and s.switch_on("year_in_title")
    # Switching it on again drops the older steps' settings
    s.set_switch_on("junk_in_titles", True)
    assert s.switch_on("junk_in_titles") and "hashtags" not in sanitize_settings.load_overrides()


def test_a_title_year_is_kept_even_behind_other_junk():
    entry = SongEntry({"artist_name": "A", "title": "Song (2020) (Official Video)"}, source=s.SOURCE_YOUTUBE)
    s.sanitize_metadata(entry, s.SOURCE_YOUTUBE)
    assert entry.title == "Song" and entry.hints.year == 2020
    assert s.strip_title_year("(1991)") == "(1991)" and s.strip_title_year("Song (Live 1998)") == "Song (Live 1998)"


def test_featured_artists_are_read_before_the_junk_is_cut():
    entry = s.sanitize_metadata(SongEntry({"artist_name": "SASSJA", "title": "Savana_feat_Darko_Rundek_Official_Video"}),
                                s.SOURCE_MP3)
    assert (entry.title, entry.feat_artists) == ("Savana", ["Darko Rundek"])
    # With nowhere to keep them, the credits stay in the title
    assert s.sanitize_field("Song ft. X", s.TITLE, s.SOURCE_MP3) == "Song ft. X"


def test_a_review_step_asks_only_in_the_database():
    # "Вjörk": a Cyrillic "В" in a Latin name - fixed as it comes in, offered for a stored one
    assert s.sanitize_field("Вjörk", s.ARTIST, s.SOURCE_MP3) == "Björk"
    assert s.sanitize_field("Вjörk", s.ARTIST, s.SOURCE_STORED) == "Вjörk"
    assert s.sanitize_field("Вjörk", s.ARTIST, s.SOURCE_MANUAL) == "Вjörk"


@pytest.mark.parametrize("title, expected", [
    ("ARMANI TOP (prod.bovskey)", "ARMANI TOP"),
    ("Одна (prod.by LFox)", "Одна"),
    ("PATOLOVE (PROD. AETHERBOY1)", "PATOLOVE"),
    ("Piękny świat (prod. Gibbs x Druid)", "Piękny świat"),
    ("Wszystko gra (prod. @atutowy)", "Wszystko gra"),
    ("Piekarenka, ale to '96 (prod. by Own Dialect)", "Piekarenka, ale to '96"),
    ("Song [produced by Y]", "Song"),
    ("Kosmos (prod. Homex)[KOSMOS]", "Kosmos[KOSMOS]"),
    ("WYTRAWNE (Z NUTĄ DESPERACJI) (prod. Catz 'n Dogz)", "WYTRAWNE (Z NUTĄ DESPERACJI)"),
    ("A (prod. X) B", "A B"),
    # A whole word only, and never the whole title
    ("Bruttosozialprodukt", "Bruttosozialprodukt"),
    ("Song (Producer's Cut)", "Song (Producer's Cut)"),
    ("Firestarter (Prodigy Remix)", "Firestarter (Prodigy Remix)"),
    ("(prod. X)", "(prod. X)"),
])
def test_strip_producer_credit(title, expected):
    assert strip_producer_credit(title) == expected


def test_a_producer_credit_goes_as_it_comes_in_and_is_only_offered_in_the_database():
    assert s.sanitize_field("Sam (prod. Gibbs)", s.TITLE, s.SOURCE_REQUEST) == "Sam"
    assert s.sanitize_field("Sam (prod. Gibbs)", s.TITLE, s.SOURCE_STORED) == "Sam (prod. Gibbs)"
    assert s.sanitize_changes("Sam (prod. Gibbs)", s.TITLE, s.SOURCE_STORED) == \
        [("producer_credit", "Sam (prod. Gibbs)", "Sam")]
    assert s.sanitize_field("Sam (prod. Gibbs)", s.ARTIST, s.SOURCE_MP3) == "Sam (prod. Gibbs)"


def test_clean_names_runs_every_step_that_is_on(settings_file):
    from utils.database import name_cleanup
    before, after = name_cleanup.stored_step_passes()
    assert before == ["html_entities", "cp1250_mojibake"]
    assert after == ["media_extension", "file_name_artifacts", "invisible_chars", "whitespace", "title_year",
                     "artist_in_title", "site_names", "topic_channel", "band_word", "pipe_suffix", "junk_brackets_anywhere", "hashtags",
                     "junk_marker_brackets", "junk_phrases", "title_year", "junk_marker_brackets", "quote_marks",
                     "whitespace", "edge_separators"]
    s.set_switch_on("artist_in_title", False)
    assert "artist_in_title" not in name_cleanup.stored_step_passes()[1]


@pytest.mark.parametrize("artist, expected", [
    ("Igo - Topic", "Igo"),
    ("WALK THE MOON - TOPIC", "WALK THE MOON"),
    # A generic channel is no artist, once its " - Topic" is gone either
    ("Release - Topic", "Release - Topic"),
    ("various artists - topic", "various artists - topic"),
    # Only the channel's own ending
    ("Hot-Topic", "Hot-Topic"),
    ("Hot Topic", "Hot Topic"),
    ("Topic", "Topic"),
    ("Le Tigre - Hot Topic", "Le Tigre - Hot Topic"),
    (" - Topic", " - Topic"),
])
def test_strip_topic_channel(artist, expected):
    assert s.strip_topic_channel(artist) == expected


def test_topic_channel_goes_only_from_an_artist_and_has_its_switch(settings_file):
    assert s.sanitize_field("Igo - Topic", s.ARTIST, s.SOURCE_MP3) == "Igo"
    assert s.sanitize_field("Igo - Topic", s.ARTIST, s.SOURCE_REQUEST) == "Igo"
    assert s.sanitize_field("Igo - Topic", s.ARTIST, s.SOURCE_MANUAL) == "Igo - Topic"  # typed by hand
    assert s.sanitize_field("Hot - Topic", s.TITLE, s.SOURCE_MP3) == "Hot - Topic"
    assert s.sanitize_field("Hot - Topic", s.ALBUM, s.SOURCE_MP3) == "Hot - Topic"
    s.set_switch_on("topic_channels", False)
    assert s.sanitize_field("Igo - Topic", s.ARTIST, s.SOURCE_MP3) == "Igo - Topic"


@pytest.mark.parametrize("artist, expected", [
    ("Zespół Hurt", "Hurt"),
    ("Zespol Hurt", "Hurt"),
    ("ZESPÓŁ HURT", "HURT"),
    ("zespół Czerwone Gitary", "Czerwone Gitary"),
    # The word is the name, or part of it
    ("Zespół", "Zespół"),
    ("Zespół ", "Zespół "),
    ("Zespół Pieśni i Tańca Śląsk", "Zespół Pieśni i Tańca Śląsk"),
    ("ZESPOL PIESNI I TANCA SLASK", "ZESPOL PIESNI I TANCA SLASK"),
    ("Zespół Wokalny Alla Polacca", "Zespół Wokalny Alla Polacca"),
    # Only the whole word, only at the start
    ("Zespółka Y", "Zespółka Y"),
    ("Hurt Zespół", "Hurt Zespół"),
    ("Grupa Operacyjna", "Grupa Operacyjna"),
])
def test_strip_band_word(artist, expected):
    assert s.strip_band_word(artist) == expected


def test_band_word_goes_only_from_an_artist_and_has_its_switch(settings_file):
    assert s.sanitize_field("Zespół Hurt", s.ARTIST, s.SOURCE_YOUTUBE) == "Hurt"
    assert s.sanitize_field("Zespół Hurt", s.ARTIST, s.SOURCE_MP3) == "Hurt"
    assert s.sanitize_field("Zespół Hurt", s.ARTIST, s.SOURCE_MANUAL) == "Zespół Hurt"  # typed by hand
    assert s.sanitize_field("Zespół Hurt", s.TITLE, s.SOURCE_MP3) == "Zespół Hurt"
    assert s.sanitize_field("Zespół Hurt", s.ALBUM, s.SOURCE_MP3) == "Zespół Hurt"
    s.set_switch_on("band_word", False)
    assert s.sanitize_field("Zespół Hurt", s.ARTIST, s.SOURCE_YOUTUBE) == "Zespół Hurt"


def test_clean_names_takes_the_artist_out_of_a_title_it_starts_with():
    from types import SimpleNamespace
    from utils.database import name_cleanup
    free_bird = SimpleNamespace(title="Lynyrd Skynyrd - Free Bird", album=None,
                                artist=SimpleNamespace(id=1, name="Lynyrd Skynyrd"))
    white_queen = SimpleNamespace(title="White Queen", album=None, artist=SimpleNamespace(id=2, name="Queen"))
    name_cleanup.apply_step([free_bird, white_queen], "artist_in_title")
    assert (free_bird.title, white_queen.title) == ("Free Bird", "White Queen")


@pytest.mark.parametrize("artist, title, expected", [
    # A tag artist naming only one of those heading the title (real file)
    ("ROSÉ", "ROSÉ & Bruno Mars - APT. (Official Music Video)", ("ROSÉ & Bruno Mars", "APT.")),
    ("Bruno Mars", "ROSÉ & Bruno Mars - APT.", ("ROSÉ & Bruno Mars", "APT.")),
    ("A", "A x B / Song", ("A x B", "Song")),
    # Only the artist heads it: just the repeated name goes
    ("Lynyrd Skynyrd", "Lynyrd Skynyrd - Free Bird", ("Lynyrd Skynyrd", "Free Bird")),
    # Not the artist's names before the separator
    ("Hey", "Old Love / New Love", ("Hey", "Old Love / New Love")),
    ("Hey", "A & B - Song", ("Hey", "A & B - Song")),
    # The artist is part of one name there, not one of the names
    ("Rose", "Rose Royce & Bruno - Song", ("Rose", "Rose Royce & Bruno - Song")),
])
def test_artists_heading_a_title_become_the_entrys_artist(artist, title, expected):
    metadata = s.sanitize_metadata({"artist_name": artist, "title": title}, s.SOURCE_MP3)
    assert (metadata["artist_name"], metadata["title"]) == expected


def test_clean_names_leaves_artists_heading_a_title_alone():
    # The artist row is shared by its songs, so a stored song's stays
    from types import SimpleNamespace
    from utils.database import name_cleanup
    song = SimpleNamespace(title="ROSÉ & Bruno Mars - APT.", album=None, artist=SimpleNamespace(id=1, name="ROSÉ"))
    name_cleanup.apply_step([song], "artist_in_title")
    assert (song.title, song.artist.name) == ("ROSÉ & Bruno Mars - APT.", "ROSÉ")


def test_a_stored_name_is_never_cleaned_to_nothing():
    from types import SimpleNamespace
    from utils.database import name_cleanup
    song = SimpleNamespace(title="y2meta.com", album="Album (greenmp3.com)", artist=SimpleNamespace(id=1, name="A"))
    changes = name_cleanup.apply_step([song], "site_names")
    assert song.title == "y2meta.com" and song.album == "Album"
    assert [c.fields for c in changes] == [["album: 'Album'"]]


def test_the_menu_switches_steps_for_everything(settings_file, monkeypatch, capsys):
    import menu.main_menu  # noqa: F401 - imported first, as main.py does
    from menu.main_menu.settings import name_cleanup as menu

    class _Answer:
        def __init__(self, value):
            self.value = value

        def ask(self):
            return self.value

    shown = []
    everything_but_quotes = [n for n in s.SWITCHES if n != "quote_marks"]

    def checkbox(message, choices):
        shown.append([c.title for c in choices])
        return _Answer(picked)

    monkeypatch.setattr(menu.questionary, "checkbox", checkbox)
    picked = everything_but_quotes
    menu.name_cleanup_menu()
    out = capsys.readouterr().out
    assert shown[0][:3] == ["HTML entities", "Incorrectly encoded characters", "File names in titles"]
    assert out.index("Characters") < out.index("What the title says") < out.index("Removing") < out.index("Tidying")
    assert not s.switch_on("quote_marks") and "Off: Quote marks around a title." in out
    picked = list(s.SWITCHES)
    menu.name_cleanup_menu()
    out = capsys.readouterr().out
    assert s.switch_on("quote_marks") and "On: Quote marks around a title." in out and "Clean names" in out


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
