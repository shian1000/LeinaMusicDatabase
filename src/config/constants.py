"""
Application-wide constants and configuration values.

This module centralizes all magic numbers, hardcoded strings, and configuration
values used throughout the application. This makes them easier to maintain and
modify in one place.
"""

# ==================== UI Constants ====================

# File browser and menu constants
RECENT_DIRS_FILE = "recent_dirs.json"
MAX_RECENT_DIRS = 5

# File browser menu options
FILE_BROWSER_SELECT_OPTION = "[SELECT THIS DIRECTORY]"
FILE_BROWSER_BACK_OPTION = "<= [Go Back]"

# Menu labels and defaults
DEFAULT_MENU_EXIT_LABEL = "Back"
DEFAULT_MENU_PICK_QUESTION = "Pick one"
DEFAULT_MENU_BACK_LABEL = "Back"
DEFAULT_FILE_BROWSER_QUESTION = "Pick a destination."

# Similarity thresholds
FILE_SIMILARITY_THRESHOLD = 90  # Percentage (0-100)
SPELLING_CHECK_THRESHOLD = 0.7  # Decimal (0-1)

# Search constants
DEFAULT_SEARCH_MODE = "Song"
DEFAULT_SEARCH_QUESTION = "What query do you wish to search for: "

# ==================== Database Constants ====================

# Search categories
SONG_CATEGORY_TITLE = "title"
SONG_CATEGORY_ARTIST = "artist name"
SONG_CATEGORY_ALBUM = "album"
SONG_CATEGORY_YEAR = "year"
SONG_CATEGORY_LANGUAGE = "language"
SONG_CATEGORY_ORIGIN = "artist origin"
SONG_CATEGORY_TAG = "tag"
SONG_CATEGORY_ARTIST_ID = "artist id"

ARTIST_CATEGORY_NAME = "artist name"
ARTIST_CATEGORY_ORIGIN = "artist origin"
ARTIST_CATEGORY_ID = "artist id"

SEARCH_CATEGORY_NAME = "name"

# ==================== API Constants ====================

# MusicBrainz API
MUSICBRAINZ_API_BASE_URL = "https://musicbrainz.org/ws/2/recording/"
MUSICBRAINZ_API_USER_AGENT = "MyMusicApp/1.0.0 ( contact@example.com )"
MUSICBRAINZ_API_LIMIT = 20

# ==================== System Constants ====================

# Operating system commands
CLEAR_SCREEN_UNIX = "clear"
CLEAR_SCREEN_WINDOWS = "cls"

# Text processing
TEXT_NORMALIZATION_REPLACEMENTS = {
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
    "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    "à": "a", "â": "a", "ä": "a", "é": "e", "è": "e",
    "ê": "e", "ë": "e", "î": "i", "ï": "i", "ô": "o",
    "ö": "o", "û": "u", "ü": "u", "ç": "c",
}

SPELLCHECK_MARKER = "[exact]"
SPELLCHECK_SUBSTRING_MARKER = "[substring]"

# ==================== String Patterns ====================

# File naming
TEXT_SEPARATOR = "_"

# SMB Protocol
SMB_PROTOCOL_PREFIX = "smb://"

# ==================== Default Values ====================

# Default query modes
QUERY_MODE_ARTIST = "Artist"
QUERY_MODE_SONG = "Song"

# Default dialog choices
DIALOG_NO_RESULT = -1
