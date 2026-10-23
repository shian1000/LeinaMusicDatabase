"""normalizer.split_artist_title(): the three stages that split an mp3 file
name or a YouTube title into (artist, title)."""
import pytest

from utils.common.normalizer import split_artist_title


@pytest.mark.parametrize("name, expected", [
    # Stage 1: a spaced separator, both parts stripped
    ("Queen - Bohemian Rhapsody", ("Queen", "Bohemian Rhapsody")),
    ("Zdob și Zdub — La mijloc", ("Zdob și Zdub", "La mijloc")),
    ("Queen  - Song", ("Queen", "Song")),
    (" Queen - Song ", ("Queen", "Song")),
    ("Jay-Z - Song", ("Jay-Z", "Song")),
    ("AC_DC - Back in Black", ("AC_DC", "Back in Black")),
    # Stage 2: a bare separator
    ("Emade-Pierwszy stopień wtajemniczenia", ("Emade", "Pierwszy stopień wtajemniczenia")),
    ("Die Ärzte _Unrockbar_", ("Die Ärzte", "Unrockbar")),
    ("Haiku Garden ~ Avalanš", ("Haiku Garden", "Avalanš")),
    ("AC_DC-Thunderstruck", ("AC_DC", "Thunderstruck")),
    ("AC_DC_-_Thunderstruck", ("AC_DC", "Thunderstruck")),
    ("X-Ray Spex-Oh Bondage", ("X-Ray Spex", "Oh Bondage")),
    # Stage 3: two spaces
    ("Akiko Yano  Iroha Ni Konpeitou", ("Akiko Yano", "Iroha Ni Konpeitou")),
])
def test_splits(name, expected):
    assert split_artist_title(name) == expected


@pytest.mark.parametrize("name", [
    "Jay-Z Song",        # the hyphen is part of the name, not a separator
    "Svefn-g-englar",
    "Bohemian Rhapsody",
    "Queen - ",          # an empty part
    " - Song",
    "",
])
def test_no_split_is_none_none(name):
    assert split_artist_title(name) == (None, None)
