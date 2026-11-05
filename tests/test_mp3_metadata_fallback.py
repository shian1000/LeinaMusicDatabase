from pathlib import Path
import sys
import os
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.discoveries.mp3_utils import extract_metadata_with_fallback, extract_mp3_metadata


def test_extract_metadata_with_fallback_uses_filename_when_title_artist_are_missing():
    file_path = Path("import/Nneka - Kangpe.mp3")

    with patch("utils.discoveries.mp3_utils.extract_mp3_metadata", return_value={
        "title": "Unknown Title",
        "artist_name": "Unknown Artist",
        "album": None,
        "year": "2008",
        "language": "English",
        "origin": None,
    }):
        with patch("utils.discoveries.mp3_utils.extract_unknown_data", return_value=("Nneka", "Kangpe")):
            metadata = extract_metadata_with_fallback(file_path)

    assert metadata["artist_name"] == "Nneka"
    assert metadata["title"] == "Kangpe"
    assert metadata["album"] is None


def _read_tags(tags: dict) -> dict:
    """extract_mp3_metadata() of a file holding `tags`."""
    with patch("utils.discoveries.mp3_utils.MP3"), patch("utils.discoveries.mp3_utils.EasyID3", return_value=tags):
        return extract_mp3_metadata(Path("any.mp3"))


@pytest.mark.parametrize("artist", ["Nieznany wykonawca", "NIEZNANY WYKONAWCA", " unknown artist "])
def test_a_rippers_artist_counts_as_missing(artist):
    assert _read_tags({"artist": [artist]})["artist_name"] == "Unknown Artist"


@pytest.mark.parametrize("title", ["Ścieżka 15", "ścieżka 3", "Sciezka 3", "Utwór 7", "Utwor 7", "Track 05", "TRACK5"])
def test_a_rippers_title_counts_as_missing(title):
    assert _read_tags({"title": [title]})["title"] == "Unknown Title"


@pytest.mark.parametrize("album", ["Nieznany album (2018-09-10 12:31:53)", "NIEZNANY ALBUM", "Unknown Album (1/2/2010)"])
def test_a_rippers_album_counts_as_missing(album):
    assert _read_tags({"album": [album]})["album"] is None


@pytest.mark.parametrize("title", ["Track Star", "Track", "Ścieżka dźwiękowa", "1979", "Track 5 Mix", "Fast Track 5"])
def test_a_real_title_stays(title):
    assert _read_tags({"title": [title]})["title"] == title


@pytest.mark.parametrize("artist", ["Nieznany", "Unknown Mortal Orchestra", "Track 3"])
def test_a_real_artist_stays(artist):
    assert _read_tags({"artist": [artist]})["artist_name"] == artist


def test_a_real_album_stays():
    assert _read_tags({"album": ["Unknown Album Of Songs"]})["album"] == "Unknown Album Of Songs"


def _fallback(tags: dict, from_name: tuple) -> dict:
    with patch("utils.discoveries.mp3_utils.extract_mp3_metadata", return_value=tags), \
            patch("utils.discoveries.mp3_utils.extract_unknown_data", return_value=from_name):
        return extract_metadata_with_fallback(Path("any.mp3"))


def test_the_file_name_replaces_only_the_names():
    metadata = _fallback({"title": "Unknown Title", "artist_name": "Unknown Artist", "album": "Mamut", "year": "2016",
                          "language": "Polish", "origin": "Poland"}, ("Ørganek", "Wiosna"))

    assert metadata == {"title": "Wiosna", "artist_name": "Ørganek", "album": "Mamut", "year": "2016",
                        "language": "Polish", "origin": "Poland"}


def test_a_real_artist_stays_when_the_file_name_has_none():
    metadata = _fallback({"title": "Unknown Title", "artist_name": "Ørganek", "album": None, "year": None,
                          "language": "Unknown", "origin": None}, (None, None))

    assert (metadata["artist_name"], metadata["title"]) == ("Ørganek", None)


def test_a_real_title_stays_when_the_file_name_has_no_artist():
    metadata = _fallback({"title": "Wiosna", "artist_name": "Unknown Artist", "album": None, "year": None,
                          "language": "Unknown", "origin": None}, (None, None))

    assert (metadata["artist_name"], metadata["title"]) == (None, "Wiosna")


def test_no_readable_tags_give_the_defaults():
    with patch("utils.discoveries.mp3_utils.extract_mp3_metadata", side_effect=ValueError("no tags")), \
            patch("utils.discoveries.mp3_utils.extract_unknown_data", return_value=("Ørganek", "Wiosna")):
        metadata = extract_metadata_with_fallback(Path("any.mp3"))

    assert metadata == {"title": "Wiosna", "artist_name": "Ørganek", "album": None, "year": None,
                        "language": "Unknown", "origin": None}


def test_a_ripped_file_reads_as_its_name_with_its_year(tmp_path):
    from utils.discoveries.import_data_from_mp3_tags import read_file_metadata

    file = tmp_path / "Ørganek - Wiosna.mp3"
    with patch("utils.discoveries.mp3_utils.extract_mp3_metadata", side_effect=lambda f: _read_tags({
        "artist": ["Nieznany wykonawca"], "title": ["Ścieżka 15"], "album": ["Nieznany album (2018-09-10 12:31:53)"],
        "date": ["2016"], "language": ["Polish"]})), \
            patch("utils.discoveries.import_data_from_mp3_tags.audio_duration", return_value=208):
        metadata = read_file_metadata(file)

    assert (metadata.artist, metadata.title, metadata.album, metadata.year, metadata.language) == \
        ("Ørganek", "Wiosna", None, "2016", "Polish")
