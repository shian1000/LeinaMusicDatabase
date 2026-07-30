from pathlib import Path
import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.discoveries.mp3_utils import extract_metadata_with_fallback


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
