"""Unit tests for import_data_from_mp3_tags module."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

from utils.discoveries.import_data_from_mp3_tags import (
    _find_exact_match,
    _check_for_similar_song,
    extract_metadata_with_fallback,
    upsert_song,
    apply_tag,
)
from utils.database.datatables import Song, Artist


class TestFindExactMatch:
    """Tests for _find_exact_match helper."""
    
    def test_find_exact_match_when_exists(self):
        """Should return song when exact normalized match found."""
        # Setup
        song1 = Mock(spec=Song)
        song1.title = "Hello World"
        song1.id = 1
        
        song2 = Mock(spec=Song)
        song2.title = "Goodbye World"
        song2.id = 2
        
        mock_session = Mock()
        mock_query = Mock()
        mock_filter = Mock()
        mock_filter.all.return_value = [song1, song2]
        mock_query.filter.return_value = mock_filter
        mock_session.query.return_value = mock_query
        
        # Execute
        result = _find_exact_match(mock_session, artist_id=1, normalized_title="hello world")
        
        # Assert
        assert result is not None
        assert result.id == 1
    
    def test_find_exact_match_when_not_exists(self):
        """Should return None when no exact match found."""
        song1 = Mock(spec=Song)
        song1.title = "Different Song"
        
        mock_session = Mock()
        mock_query = Mock()
        mock_filter = Mock()
        mock_filter.all.return_value = [song1]
        mock_query.filter.return_value = mock_filter
        mock_session.query.return_value = mock_query
        
        result = _find_exact_match(mock_session, artist_id=1, normalized_title="hello world")
        
        assert result is None


class TestCheckForSimilarSong:
    """Tests for _check_for_similar_song helper."""
    
    def test_no_similar_song_found(self):
        """Should return None when no similar song found."""
        song1 = Mock(spec=Song)
        song1.title = "Different"
        song1.artist = Mock()
        song1.artist.name = "Other Artist"
        
        mock_session = Mock()
        mock_query = Mock()
        mock_query.all.return_value = [song1]
        mock_session.query.return_value = mock_query
        
        with patch('utils.discoveries.import_data_from_mp3_tags.are_song_entries_similar', return_value=False):
            result = _check_for_similar_song(mock_session, "New Song", "New Artist", interactive=False)
        
        assert result is None
    
    def test_similar_song_found_non_interactive(self):
        """Should return the similar song when found in non-interactive mode."""
        song1 = Mock(spec=Song)
        song1.title = "Similar Title"
        song1.artist = Mock()
        song1.artist.name = "Same Artist"
        
        mock_session = Mock()
        mock_query = Mock()
        mock_query.all.return_value = [song1]
        mock_session.query.return_value = mock_query
        
        with patch('utils.discoveries.import_data_from_mp3_tags.are_song_entries_similar', return_value=True):
            result = _check_for_similar_song(mock_session, "Similar Title", "Same Artist", interactive=False)
        
        assert result is song1


class TestUpsertSong:
    """Tests for upsert_song function."""
    
    def test_exact_match_skip_mode(self):
        """Should skip when exact match found in skip mode."""
        existing_song = Mock(spec=Song)
        existing_song.id = 1
        existing_song.title = "Test Song"
        existing_song.year = 2020
        existing_song.language = "en"
        existing_song.album = "Test Album"
        
        artist = Mock(spec=Artist)
        artist.id = 1
        artist.name = "Test Artist"
        
        mock_session = Mock()
        
        metadata = {
            "title": "Test Song",
            "album": "Test Album",
            "language": "en",
            "year": "2021",
        }
        
        with patch('utils.discoveries.import_data_from_mp3_tags._find_exact_match', return_value=existing_song):
            with patch('utils.discoveries.import_data_from_mp3_tags.extract_unknown_data', side_effect=lambda m, f: (m.get("artist_name", ""), m.get("title", ""))):
                song, status = upsert_song(mock_session, artist, metadata, mode="skip", file=Path("test.mp3"), interactive=False)
        
        assert status == "skipped"
        assert song is existing_song
    
    def test_exact_match_update_mode(self):
        """Should update when exact match found in update mode."""
        existing_song = Mock(spec=Song)
        existing_song.id = 1
        existing_song.title = "Test Song"
        existing_song.year = 2020
        existing_song.language = "en"
        existing_song.album = "Test Album"
        
        artist = Mock(spec=Artist)
        artist.id = 1
        artist.name = "Test Artist"
        
        mock_session = Mock()
        
        metadata = {
            "title": "Test Song",
            "album": "New Album",
            "language": "es",
            "year": "2021",
        }
        
        with patch('utils.discoveries.import_data_from_mp3_tags._find_exact_match', return_value=existing_song):
            with patch('utils.discoveries.import_data_from_mp3_tags.extract_unknown_data', side_effect=lambda m, f: (m.get("artist_name", ""), m.get("title", ""))):
                song, status = upsert_song(mock_session, artist, metadata, mode="update", file=Path("test.mp3"), interactive=False)
        
        assert status == "updated"
        assert existing_song.year == 2021
        assert existing_song.language == "es"
        assert mock_session.commit.called
    
    def test_add_new_song_when_no_match(self):
        """Should add new song when no exact match and no similar song."""
        artist = Mock(spec=Artist)
        artist.id = 1
        artist.name = "Test Artist"
        
        mock_session = Mock()
        
        metadata = {
            "title": "New Song",
            "album": "New Album",
            "language": "en",
            "year": "2021",
        }
        
        with patch('utils.discoveries.import_data_from_mp3_tags._find_exact_match', return_value=None):
            with patch('utils.discoveries.import_data_from_mp3_tags._check_for_similar_song', return_value=None):
                with patch('utils.discoveries.import_data_from_mp3_tags.extract_unknown_data', side_effect=lambda m, f: (m.get("artist_name", ""), m.get("title", ""))):
                    with patch('utils.discoveries.import_data_from_mp3_tags.Song') as MockSong:
                        song_instance = Mock(spec=Song)
                        MockSong.return_value = song_instance
                        
                        song, status = upsert_song(mock_session, artist, metadata, mode="skip", file=Path("test.mp3"), interactive=False)
        
        assert status == "added"
        assert mock_session.add.called
        assert mock_session.commit.called


class TestApplyTag:
    """Tests for apply_tag function."""
    
    def test_apply_tag_success(self):
        """Should call add_tag_to_song with correct tag name."""
        song = Mock(spec=Song)
        song.id = 1
        
        file = Path("/music/folder/subfolder/song.mp3")
        root_folder = Path("/music/folder")
        
        with patch('utils.discoveries.import_data_from_mp3_tags.add_tag_to_song', return_value=True) as mock_add_tag:
            apply_tag(song, file, root_folder)
        
        # Verify add_tag_to_song was called with correct tag name
        mock_add_tag.assert_called_once_with(song, "subfolder")
    
    def test_apply_tag_nested_folders(self):
        """Should handle nested folder structures correctly."""
        song = Mock(spec=Song)
        song.id = 1
        
        file = Path("/music/folder/rock/classic/song.mp3")
        root_folder = Path("/music/folder")
        
        with patch('utils.discoveries.import_data_from_mp3_tags.add_tag_to_song', return_value=True) as mock_add_tag:
            apply_tag(song, file, root_folder)
        
        # Should create tag from relative path including nested folders
        expected_tag = "rock/classic"
        mock_add_tag.assert_called_once_with(song, expected_tag)
    
    def test_apply_tag_failure(self):
        """Should handle failure gracefully."""
        song = Mock(spec=Song)
        song.id = 1
        
        file = Path("/music/folder/subfolder/song.mp3")
        root_folder = Path("/music/folder")
        
        with patch('utils.discoveries.import_data_from_mp3_tags.add_tag_to_song', return_value=False):
            with patch('utils.discoveries.import_data_from_mp3_tags.slog') as mock_slog:
                apply_tag(song, file, root_folder)
        
        # Should log the failure
        assert mock_slog.called


class TestExtractMetadataWithFallback:
    """Tests for _extract_metadata_with_fallback helper."""
    
    def test_extract_metadata_success_from_id3(self):
        """Should use ID3 metadata when available."""
        file = Path("/music/test_artist_-_test_song.mp3")
        
        mock_metadata = {
            "title": "Test Song",
            "artist_name": "Test Artist",
            "album": "Test Album",
            "year": "2024",
            "language": "en",
            "origin": None,
        }
        
        with patch('utils.discoveries.import_data_from_mp3_tags.extract_mp3_metadata', return_value=mock_metadata):
            result = extract_metadata_with_fallback(file)
        
        assert result == mock_metadata
        assert result["title"] == "Test Song"
        assert result["artist_name"] == "Test Artist"
    
    def test_extract_metadata_fallback_to_filename(self):
        """Should fall back to filename parsing when ID3 extraction fails."""
        file = Path("/music/Artist Name_-_Song Title.mp3")
        
        with patch('utils.discoveries.import_data_from_mp3_tags.extract_mp3_metadata', side_effect=Exception("Bad ID3 tags")):
            with patch('utils.discoveries.import_data_from_mp3_tags.extract_unknown_data', return_value=("Artist Name", "Song Title")):
                result = extract_metadata_with_fallback(file)
        
        # Should have successfully extracted from filename
        assert result["title"] == "Song Title"
        assert result["artist_name"] == "Artist Name"
        assert result["album"] is None
        assert result["language"] == "Unknown"
    
    def test_extract_metadata_complete_failure(self):
        """Should raise ValueError when both ID3 and fallback fail."""
        file = Path("/music/unknown_file.mp3")
        
        with patch('utils.discoveries.import_data_from_mp3_tags.extract_mp3_metadata', side_effect=Exception("Bad ID3")):
            with patch('utils.discoveries.import_data_from_mp3_tags.extract_unknown_data', side_effect=Exception("Bad filename")):
                with pytest.raises(ValueError, match="Could not extract metadata"):
                    extract_metadata_with_fallback(file)
    
    def test_extract_metadata_fallback_returns_complete_dict(self):
        """Should return complete metadata dict from fallback with all required keys."""
        file = Path("/music/My Band_-_My Song.mp3")
        
        with patch('utils.discoveries.import_data_from_mp3_tags.extract_mp3_metadata', side_effect=Exception("No tags")):
            with patch('utils.discoveries.import_data_from_mp3_tags.extract_unknown_data', return_value=("My Band", "My Song")):
                result = extract_metadata_with_fallback(file)
        
        # Verify all required keys are present
        required_keys = {"title", "artist_name", "album", "year", "language", "origin"}
        assert set(result.keys()) == required_keys
        assert result["title"] == "My Song"
        assert result["artist_name"] == "My Band"
        assert result["album"] is None
        assert result["year"] is None
        assert result["language"] == "Unknown"
        assert result["origin"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
