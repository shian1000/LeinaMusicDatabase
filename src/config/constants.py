"""
Application-wide constants and configuration values.

This module centralizes all magic numbers, hardcoded strings, and configuration
values used throughout the application. This makes them easier to maintain and
modify in one place.
"""
from pathlib import Path

# The repository folder. Every state file below is resolved against it, never
# against the current directory, so the app, the systemd timer and tools/
# started from anywhere read the same settings (and the same database folder
# override).
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ==================== UI Constants ====================

# File browser and menu constants
RECENT_DIRS_FILE = PROJECT_ROOT / "recent_dirs.json"
MAX_RECENT_DIRS = 5

# Discovery module settings (enabled state + fetch order), persisted as JSON.
# Order here is only the seed used the first time the file is created — after
# that, the JSON file is the source of truth and this is ignored.
DISCOVERY_MODULES_CONFIG_FILE = PROJECT_ROOT / "discovery_modules_config.json"

# Same idea as DISCOVERY_MODULES_CONFIG_FILE, but for "Fill missing data ->
# Years": a separate config because only some modules implement
# get_release_year(), so the set of available modules (and a sensible order)
# differs from the album fetchers.
DISCOVERY_MODULES_YEAR_CONFIG_FILE = PROJECT_ROOT / "discovery_modules_config_years.json"

# Optional override for Settings.database_dir (the folder holding music.db /
# tag.db), persisted as JSON. Read once at startup, so a change made through
# the Settings menu only takes effect after the app is restarted.
DATABASE_LOCATION_CONFIG_FILE = PROJECT_ROOT / "database_location_config.json"

# Per-module invocation/success counters for the Statistics menu, persisted
# as JSON and keyed by module id (filename stem) for the same reason as
# DISCOVERY_MODULES_CONFIG_FILE: stable across MODULE_NAME renames.
DISCOVERY_STATS_FILE = PROJECT_ROOT / "discovery_fetcher_stats.json"

# Same as DISCOVERY_STATS_FILE, but counters for "Fill missing data -> Years"
# lookups, kept separate so they don't conflate with the album fetchers'
# invocation/success rates in the Statistics menu.
DISCOVERY_STATS_YEAR_FILE = PROJECT_ROOT / "discovery_fetcher_stats_years.json"

# Cap on how many songs a single "Fetch database data" run (e.g. Fill missing
# albums) processes at once, persisted as JSON. Keeps a single run from
# churning through the whole database when only a quick batch is wanted.
FETCH_DATA_CONFIG_FILE = PROJECT_ROOT / "fetch_data_config.json"
DEFAULT_MAX_SONGS_PER_FETCH = 50

# "Create playlist from folder": a file whose title matches a song exactly but
# whose tag artist is only spelt like the song's ("Liiquid tension experiment",
# 0.98) is matched without asking when the artist similarity reaches this;
# below it, it's only suggested. Settings -> "Auto-accept similar artist
# names" (persisted as JSON); 1.0 turns it off.
FILE_MATCHING_CONFIG_FILE = PROJECT_ROOT / "file_matching_config.json"
DEFAULT_AUTO_ACCEPT_ARTIST_SIMILARITY = 0.9
MIN_AUTO_ACCEPT_ARTIST_SIMILARITY = 0.8

# Database sharing for the MusicDatabaseApp mobile client (see
# docs/runbooks/database.md -> "Sharing with the mobile app"). The phone
# downloads <folder URL>/music.db and <folder URL>/tag.db from this port over
# Tailscale; 8001 is already taken by the APK share. Changing the port or the
# file names breaks the app's contract - agree it with the app side first.
SHARING_HTTP_PORT = 8002
SHARING_SERVICE_NAME = "musicdatabase-share"
# Delay after the user's systemd manager starts (= after login, without linger)
# before the sharing server is started.
SHARING_STARTUP_DELAY = "1min"
# Requests the phone sends to the sharing server (PUT /inbox/<name>.db, see
# sharing_server.py): one file at most PHONE_REQUEST_MAX_BYTES, and none taken
# while the unprocessed ones (Settings.phone_inbox_dir / "new") reach
# PHONE_INBOX_LIMIT_BYTES. Part of the app's contract, like the port.
PHONE_REQUEST_MAX_BYTES = 1024 * 1024
PHONE_INBOX_LIMIT_BYTES = 100 * 1024 * 1024
# Request file format this version understands (request.format)
PHONE_REQUEST_FORMAT = 1
# A request package (utils/database/request_packages.py): a .zip of Leina
# Radio's outbox files, sent by hand by someone who can't reach the sharing
# server. Caps against zip bombs, on top of PHONE_REQUEST_MAX_BYTES per file.
REQUEST_PACKAGE_MAX_FILES = 1000
REQUEST_PACKAGE_MAX_BYTES = 50 * 1024 * 1024
# The public copy of music.db / tag.db published as a GitHub Release
# (utils/database/public_database.py; app ADR 0014). Phones download
# https://github.com/<GITHUB_DB_REPO>/releases/download/<tag>/<file>, so the
# tag and the asset names are the app's contract, like the sharing port.
PUBLIC_DB_RELEASE_TAG = "database"
PUBLIC_DB_RELEASE_NAME = "Public database"
PUBLIC_DB_VERSION_FILE = "version.json"
PUBLIC_DB_FORMAT = 1
# The daily publish switch and the last publish's time and result
PUBLIC_DB_STATE_FILE = PROJECT_ROOT / "data/public_database.json"
# The day main.py last ran its once-a-day tasks (utils/common/startup_settings.py)
STARTUP_STATE_FILE = PROJECT_ROOT / "data/startup_state.json"
# Where a fresh install without databases downloads the public catalog from
# (utils/database/first_run.py) when GITHUB_DB_REPO isn't set: this project's
# own release
PUBLIC_CATALOG_DEFAULT_REPO = "shian1000/LeinaMusicDatabase"
PUBLIC_CATALOG_TIMEOUT = 30
# Refuse a version.json announcing a bigger file - a sanity cap, the catalog is ~1 MiB
PUBLIC_CATALOG_MAX_BYTES = 512 * 1024 * 1024
GITHUB_API_URL = "https://api.github.com"
GITHUB_API_TIMEOUT = 20
GITHUB_UPLOAD_TIMEOUT = 120
# The automatic pass over new requests (utils/database/inbox_auto.py), run by
# a systemd user timer (auto_resolve_requests.py) this often, while the app
# isn't open
INBOX_AUTO_SERVICE_NAME = "musicdatabase-inbox"
INBOX_AUTO_INTERVAL = "15min"
INBOX_AUTO_STARTUP_DELAY = "5min"
# The review reuses the automatic pass's YouTube check while it's this fresh
INBOX_CHECK_MAX_AGE_HOURS = 24
# Why a video is in music.db's bad_youtube_videos (migration 0010). The codes
# are read by the phone client (it only shows them), so keep them stable; a
# new one needs no app change. Labels are what this app shows.
BAD_VIDEO_REMOVED = "removed"  # gone per the YouTube Data API
BAD_VIDEO_UNPLAYABLE = "unplayable"  # yt-dlp says it doesn't play here
BAD_VIDEO_REPLACED_DEAD_LINK = "replaced_dead_link"  # reported dead, replaced or set to "N/A"
BAD_VIDEO_WRONG_FOR_SONG = "wrong_for_song"  # the owner turned it down for the song
BAD_VIDEO_NO_VIDEO_FOR_SONG = "no_video_for_song"  # the owner chose "N/A" while it was suggested
BAD_VIDEO_REASONS = {
    BAD_VIDEO_REMOVED: "removed from YouTube (Data API)",
    BAD_VIDEO_UNPLAYABLE: "doesn't play here (yt-dlp)",
    BAD_VIDEO_REPLACED_DEAD_LINK: "reported dead by Leina Radio, replaced",
    BAD_VIDEO_WRONG_FOR_SONG: "turned down for this song by the owner",
    BAD_VIDEO_NO_VIDEO_FOR_SONG: 'the owner chose "N/A" (no video) for this song',
}

# Daily "is yt-dlp current?" check (utils/youtube/ytdlp_update.py)
YTDLP_PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
YTDLP_UPDATE_CHECK_TIMEOUT = 10
# Country (ISO 3166-1 alpha-2) the database's YouTube links have to play in -
# a video region-blocked here counts as not playing (videos_exist_via_api())
YOUTUBE_REGION = "PL"
# Language yt-dlp asks YouTube in when it reads a playlist page (--flat-playlist).
# yt-dlp's default, English, gets the uploader's English translation of a title
# ("Ivan NAVI - Такі Молоді" -> "So Young"); a language uploaders practically never
# translate into gives the original title, and Zulu still writes numbers the English
# way, so view counts keep parsing. Full video reads already return the original.
YTDLP_PLAYLIST_LANG = "zu"

DEFAULT_DISCOVERY_MODULE_ORDER = [
    "music_brainz_fetcher",
    "spotify_fetcher",
    "youtube_fetcher",
    "wikipedia_fetcher",
    "google_search_fetcher",
    "itunes_fetcher",
    "genius_fetcher",
]

# Seed order for the subset of modules that implement get_release_year().
# Like DEFAULT_DISCOVERY_MODULE_ORDER, only used the first time the config
# file is created; reconcile_year_discovery_config() auto-adds any other
# module that later gains a get_release_year() of its own.
DEFAULT_DISCOVERY_MODULE_YEAR_ORDER = [
    "music_brainz_fetcher",
    "google_search_fetcher",
    "spotify_fetcher",
    # Last on purpose: its upload-date years need the user's review.
    "youtube_fetcher",
]

# A fetched release year outside this range is treated as implausible (e.g. a
# scraper accidentally reading a copyright notice or an unrelated date) and
# discarded rather than written to the database.
MIN_PLAUSIBLE_RELEASE_YEAR = 1900

# How many (random) song titles "Fill missing data -> Years" shows next to an
# album it asks the user to fill in manually, as a hint which album it is.
MANUAL_YEAR_SAMPLE_SONGS = 3

# Whole-word, case-insensitive markers that flag an artist name as really being
# a soundtrack ("Tekken 5 OST"). When every album fetcher comes up empty for
# such an artist, discover_album_name() falls back to using the artist name
# itself as the album.
SOUNDTRACK_ARTIST_MARKERS = [
    "ost",
    "o.s.t.",
    "o.s.t",
    "soundtrack",
    "soundtracks",
]

# Whole-word, case-insensitive markers that flag a song as a standalone release
# (a cover, an unplugged version) when found in its title or artist name
# ("serial heartbreaker - fletcher loop cover"). When every album fetcher comes
# up empty for such a song, discover_album_name() files it under SINGLES_ALBUM.
SINGLES_MARKER = [
    "cover",
    "covers",
    "covered",
    "unplugged",
]

# Sentinel album value meaning "no real parent album". Fetchers return it for
# single-only releases, and "Fill missing data -> Years" looks these songs up
# one by one by title instead of grouping them like an album.
SINGLES_ALBUM = "Singles"

# File browser menu options
FILE_BROWSER_SELECT_OPTION = "[SELECT THIS DIRECTORY]"
FILE_BROWSER_BACK_OPTION = "<= [Go Back]"

# Menu labels and defaults
DEFAULT_MENU_EXIT_LABEL = "Back"
DEFAULT_MENU_PICK_QUESTION = "Pick one"
DEFAULT_MENU_BACK_LABEL = "Back"
DEFAULT_FILE_BROWSER_QUESTION = "Pick a destination."
FILE_BROWSER_EXIT_OPTION = "Exit file manager"

# Similarity thresholds
FILE_SIMILARITY_THRESHOLD = 90  # Percentage (0-100)
SPELLING_CHECK_THRESHOLD = 0.7  # Decimal (0-1)
SIMILARITY_THRESHOLD = 0.77 # Used when resolving duplicates
# Two artist rows offered for merging as one spelt differently ("Man I Trust" /
# "Men I Trust" 0.91) - stricter than the lookup threshold, since a merge
# moves every song: "Persona 5 OST" / "Persona 5 Strikers OST" is 0.74
SAME_ARTIST_SIMILARITY_THRESHOLD = 0.85

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

# additional_song_artists.role - the song's own songs.artist_id is always the
# primary artist; extra rows are either co-headliners ("A x B") or guests
# ("A feat. B").
ADDITIONAL_ARTIST_ROLE_MAIN = "main"
ADDITIONAL_ARTIST_ROLE_FEAT = "feat"
ADDITIONAL_ARTIST_ROLES = (ADDITIONAL_ARTIST_ROLE_MAIN, ADDITIONAL_ARTIST_ROLE_FEAT)
# How song_artist_label() joins a multi-artist song back into one string
MULTI_ARTIST_MAIN_SEPARATOR = " x "
MULTI_ARTIST_FEAT_SEPARATOR = " feat. "

# albums.type (migration 0005). NULL = not known. ALBUM_TYPE_SINGLE is only
# ever given to the one-song album of a song filed under SINGLES_ALBUM.
ALBUM_TYPE_ALBUM = "album"
ALBUM_TYPE_EP = "ep"
ALBUM_TYPE_SINGLE = "single"
ALBUM_TYPE_COMPILATION = "compilation"
ALBUM_TYPE_SOUNDTRACK = "soundtrack"
ALBUM_TYPES = (ALBUM_TYPE_ALBUM, ALBUM_TYPE_EP, ALBUM_TYPE_SINGLE, ALBUM_TYPE_COMPILATION, ALBUM_TYPE_SOUNDTRACK)

# songs.kind (migration 0014). NULL = an ordinary song. SONG_KIND_FULL_ALBUM =
# the row is a whole album played through (one video / one mp3 of the album);
# Leina Radio labels it and leaves it out of its song radio.
SONG_KIND_FULL_ALBUM = "full_album"
SONG_KINDS = (SONG_KIND_FULL_ALBUM,)
SONG_KIND_LABELS = {None: "song", SONG_KIND_FULL_ALBUM: "full album"}
# A recording at least this long (a library file, a YouTube video) may be a
# whole album: a candidate in the review, and a full album's YouTube search
# takes only videos this long. A file or video this long may match a song too
# (mixes, live sets); a shorter one never matches a full album.
FULL_ALBUM_MIN_DURATION_S = 20 * 60
# Words in a title saying the recording is a whole album ("Migration (Full
# Album)", "Legenda - cały album"). Compared after normalize(), as whole
# words. Such a title makes a new song a full album at import, and is how
# the review spots candidates; marking one offers the title without them.
FULL_ALBUM_TITLE_MARKERS = [
    "full album",
    "full album stream",
    "full lp",
    "full ep",
    "full length album",
    "full interslavic album",
    "cały album",
    "pełny album",
    "весь альбом",
    "полный альбом",
    "увесь альбом",
    "album completo",
    "álbum completo",
    "album complet",
    "ganzes album",
]
# Songs the owner said aren't full albums, never offered again by the review
FULL_ALBUM_IGNORE_FILE = PROJECT_ROOT / "data/full_album_ignore.json"

# album_covers (migration 0008)
ALBUM_COVER_SOURCE_FILE = "file"  # embedded in the user's mp3s (APIC)
ALBUM_COVER_SOURCES = (ALBUM_COVER_SOURCE_FILE, "itunes", "deezer", "musicbrainz", "youtube", "manual")
ALBUM_COVER_STATUS_FOUND = "found"
ALBUM_COVER_STATUS_MISSING = "missing"  # checked, the source has none
# Covers are stored as <database_dir>/<ALBUM_COVERS_DIRNAME>/<album_id>.jpg,
# never inside music.db
ALBUM_COVERS_DIRNAME = "covers"
# Re-encode size/quality - the phone never shows a cover bigger than this
ALBUM_COVER_MAX_SIZE = 600
ALBUM_COVER_JPEG_QUALITY = 85
# A cover outside this width/height ratio is a video frame (16:9, 4:3 YouTube
# thumbnails written in by downloaders), not album art - skipped
ALBUM_COVER_MIN_ASPECT = 0.9
ALBUM_COVER_MAX_ASPECT = 1.1
# ID3 APIC picture type 3 = "Cover (front)"
ID3_PICTURE_TYPE_FRONT_COVER = 3
# Two embedded images whose 64-bit difference hashes are at most this many
# bits apart count as the same picture (the same cover re-encoded at another
# size), so they vote together rather than as a disagreement
ALBUM_COVER_SAME_IMAGE_MAX_DISTANCE = 6

# ==================== Users (utils/database/users.py) ====================

# Lowercase only: the users folder is on exFAT (the data drive), which ignores case, so
# "Ania" and "ania" would be the same folder anyway
USERNAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,31}$"
USER_PASSWORD_MIN_LENGTH = 6
USER_DB_FILENAME = "user.db"
USER_ACCOUNT_FILENAME = "account.json"
# hashlib.scrypt cost parameters, stored with each hash so they can be raised
# later without invalidating existing passwords
PASSWORD_SCRYPT_N = 2 ** 14
PASSWORD_SCRYPT_R = 8
PASSWORD_SCRYPT_P = 1
PASSWORD_SALT_BYTES = 16

# playlists.source in a user's user.db: where the playlist came from
# (source_id says which one: the folder, or the YouTube playlist / Spotify
# playlist / Spotify album id). The phone's values, part of the playlist sync
# contract (utils/database/playlist_sync.SOURCES must match).
USER_PLAYLIST_SOURCE_FOLDER = "folder"
USER_PLAYLIST_SOURCE_YOUTUBE = "youtube"
USER_PLAYLIST_SOURCE_SPOTIFY = "spotify"
USER_PLAYLIST_SOURCE_SPOTIFY_ALBUM = "spotifyAlbum"

# tags.kind in tag.db and user.db: a mood ("Nastrój" on the phone - global in
# tag.db, a user's own in user.db) or anything else, e.g. the "spellchecked"
# marker. The phone reads only moods (utils/database/mood_sync.MOOD_KIND must match).
TAG_KIND_MOOD = "mood"
TAG_KIND_MARKER = "marker"

# What the owner can allow a user (main menu -> Users -> Permissions), kept
# in that user's account.json. Names are part of the phone contract
# (utils/database/account_auth.PERMISSIONS must match); the label is the menu's.
PERMISSION_EDIT_GLOBAL_MOODS = "edit_global_moods"
PERMISSION_APPLY_CORRECTIONS = "apply_corrections"
PERMISSION_LABELS = {
    PERMISSION_EDIT_GLOBAL_MOODS: "Edit global moods from Leina Radio (add and remove songs)",
    PERMISSION_APPLY_CORRECTIONS: "Field corrections from Leina Radio are saved without review (when nothing conflicts)",
}

# ==================== Genres (utils/database/genres.py, songs.genre) ====================

# MusicBrainz's genre list (CC0, ws/2/genre/all?fmt=txt), one per line - the
# common spelling every genre is mapped onto. Relative to src/config/.
GENRE_VOCABULARY_FILE = "musicbrainz_genres.txt"
# Other spellings -> the stored one, keyed by genres.genre_key() (lowercase,
# no diacritics, "&" = "and", no spaces or punctuation). Spellings that differ
# only in case / hyphens / spaces ("Synthpop", "hip-hop") need no entry: the
# key already finds them in the vocabulary.
GENRE_SYNONYMS = {
    "rnb": "r&b",
    "randb": "r&b",
    "rhythmandblues": "r&b",
    "dnb": "drum and bass",
    "dandb": "drum and bass",
    "drumnbass": "drum and bass",
    "rocknroll": "rock and roll",
    "altrock": "alternative rock",
    "kpop": "k-pop",
    "jpop": "j-pop",
    "jrock": "j-rock",
    "lofi": "lo-fi",
}
# Fill missing data -> Genres: at most this many genres per song, the most
# voted first, and only genres with at least this share of the top one's votes
GENRE_MAX_PER_SONG = 3
GENRE_MIN_VOTE_SHARE = 0.3
# Songs MusicBrainz had no genre for, skipped by later runs. Relative to the
# project root; delete it to look them all up again.
GENRE_LOOKUP_MISSES_FILE = PROJECT_ROOT / "data/genre_lookup_misses.json"
# Where a looked-up genre came from (genres.GenreResult.level). Song and album
# genres are saved at once; an artist's genre is listed for the owner first.
GENRE_LEVEL_SONG = "song"
GENRE_LEVEL_ALBUM = "album"
GENRE_LEVEL_ARTIST = "artist"

# ==================== Music library index (utils/database/files_index.py) ====================

FILES_DB_FILENAME = "files.db"
# Compared lowercased - the library has ".MP3" and ".Mp3" files too
LIBRARY_AUDIO_EXTENSIONS = (".mp3",)
# files.match_source: "auto" rows are rematched by every scan while they have
# no song; "manual" rows are left alone
FILE_MATCH_AUTO = "auto"
FILE_MATCH_MANUAL = "manual"

# ==================== API Constants ====================

# MusicBrainz API
MUSICBRAINZ_API_BASE_URL = "https://musicbrainz.org/ws/2/recording/"
# Root of the web service, for the other entities (artist/, release-group/)
MUSICBRAINZ_WS_ROOT = "https://musicbrainz.org/ws/2/"
# MusicBrainz asks every client to identify itself with an application name,
# version, and a real contact (an email address or a URL). A generic User-Agent
# is throttled harder and can be blocked outright. This points at the project
# repo; swap in your own contact if you fork it.
MUSICBRAINZ_API_USER_AGENT = "MusicDatabase/1.0 ( https://github.com/shian1000/LeinaMusicDatabase )"
MUSICBRAINZ_API_LIMIT = 20
# MusicBrainz's special "Various Artists" artist - a compilation's or soundtrack's
# release artist, so a song on it is never "another artist's album"
MUSICBRAINZ_VARIOUS_ARTISTS_ID = "89ad4ac3-39f7-470e-963a-56509c546377"
# (connect, read) timeout in seconds for a single MusicBrainz HTTP request, so a
# stalled connection can never hang an import forever.
MUSICBRAINZ_API_TIMEOUT = (5, 12)
# Minimum seconds between two outgoing MusicBrainz requests (process-wide). Their
# anonymous rate limit is ~1 req/s; staying just above 1s avoids the 503 storms
# and multi-second backoffs that otherwise dominate a slow import.
MUSICBRAINZ_API_MIN_INTERVAL = 1.1
# Run the broad unfielded fallback query when the precise query finds nothing.
# It roughly doubles the request count for every miss; set to False to trade a
# little recall for speed.
MUSICBRAINZ_SPELLCHECK_USE_FALLBACK = True
# Disk-backed cache of check_spelling() results, path relative to the project
# root. Delete this file to force fresh lookups of everything.
SPELLCHECK_CACHE_FILE = PROJECT_ROOT / "data/spellcheck_cache.json"
# Joined artist names ("Simon & Garfunkel") the user said are one artist, so
# "Split joined artist names" stops proposing them. Relative to the project
# root; delete it (or a line in it) to be asked again.
ARTIST_SPLIT_IGNORE_FILE = PROJECT_ROOT / "data/artist_split_ignore.json"
# Words joining two artists ("Ірина Білик і Скрябін", "Hasiok i Maciej
# Maleńczuk"). So many band names use them ("Tymon i Trupy", "Of Monsters and
# Men") that a split on one is proposed only when a part is already an artist.
ARTIST_JOINING_WORDS = ("and", "und", "et", "i", "y", "і", "и", "та")

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


# Trailing junk phrases that only describe how the video was uploaded, never
# part of the song's actual name (e.g. "Smallville - Save Me Music Video" ->
# "Smallville - Save Me"). Stripped from the end of the title automatically,
# without asking for confirmation, by both the YouTube playlist import
# (import_from_playlist.py) and Clean names
# (clean_names.py), via text_utils.strip_trailing_junk_suffix().
# Matched case-insensitively as whole words at the very end of the title,
# bracketed or not; add new phrases here as they come up. Longer phrases are listed before the shorter phrases they
# contain (e.g. "official music video" before "music video") so the whole
# annotation is stripped in one go.
# Words that stand for "&" in a title ("Angel of Small Death & the Codeine
# Scene" vs "... And The Codeine Scene"). normalize() drops "&" as punctuation,
# so the loose title match (import_engine._title_core()) drops these words too,
# on both sides. Never used for the strict "already exists?" check.
# Matching a library file to a song of its tag artist when no title matches
# exactly: a title at least this similar (whole titles, bracket text kept, e.g.
# "Losing TimeGrande Finale" ~ "Losing Time (Grand Finale)", 0.96) is only
# *suggested* (LocalSongMatcher.match_similar()); "Closing Time" scores 0.61.
FILE_TITLE_SIMILARITY_THRESHOLD = 0.85
# ... or whose title the file's starts with, whole words ("Tubthumping
# Officiel official Soundtrack World Cup 98" -> "Tubthumping"), when that
# title (its core) is at least this long - "Ja" mustn't claim every "Ja ...".
FILE_TITLE_PREFIX_MIN_LENGTH = 4

# A file with no artist whose title several songs have ("Toccata and Fugue in
# D Minor": Bach, Vanessa-Mae) is offered them to pick from, up to this many;
# more and the user searches by title instead.
FILE_TITLE_CANDIDATES_MAX = 5

# A song request whose video title is "<artist>: <title>" ("Travie McCoy:
# Billionaire ft. Bruno Mars" on the label's channel) is read that way only
# when the part before ": " has at most this many words - longer, it's more
# likely a title with a subtitle ("Star Wars: Episode IV ...").
SONG_REQUEST_COLON_ARTIST_MAX_WORDS = 4

# A song request whose video title has no separator at all ("Бумбокс і Тіна
# Кароль Безодня" on a fan channel) is read as artists + title only when it
# starts with artists the database knows by exactly that name (or the
# channel's name holds them, for all but one of them): each artist at most
# this many words, and one lone artist at least this many characters
# (name_key()), since a short name ("Yes", "Air") starts many titles.
SONG_REQUEST_TITLE_ARTIST_MAX_WORDS = 5
SONG_REQUEST_LONE_TITLE_ARTIST_MIN_CHARS = 4

# An artist a YouTube description credits that the database doesn't know is
# tried as several known artists side by side ("Fisz Emade Tworzywo" ->
# Fisz / Emade / Tworzywo Sztuczne) only up to this many words.
SONG_REQUEST_CREDIT_SPLIT_MAX_WORDS = 6

# A file nothing else found a song for is offered up to this many of the
# closest songs by score (LocalSongMatcher.match_score(): title, and the
# artist when the file has one), if they score at least the "Closest songs:
# minimum score" setting (Settings, persisted with the auto-accept one).
FILE_BEST_GUESSES_MAX = 3
# Pairing a file with a video of the YouTube playlist by hand: only this many
# of the most alike unpaired videos are listed (plus a search). A list taller
# than the terminal makes questionary draw its hidden part as blank lines.
FILE_PAIR_VIDEO_CHOICES_MAX = 10
DEFAULT_BEST_GUESS_MIN_SCORE = 0.6
BEST_GUESS_MIN_SCORE_RANGE = (0.3, 0.95)
# A suggestion (similar song, a song sharing the title, a closest song) whose
# score reaches this is taken without asking - when it's the only one that
# does - and listed after matching (a file with no artist: see
# NAMELESS_AUTO_ACCEPT_*). Settings -> "Closest songs: auto-accept score";
# 1 = always ask.
DEFAULT_CLOSEST_AUTO_ACCEPT_SCORE = 0.99
# A file with no artist is scored on its title alone, so it's auto-accepted
# only for the one song with its title, and only when the title (core) is
# distinctive enough: "Dragostea Din Tei (Numa Numa) (Medieval Cover)" yes,
# "Intro" or "Ocean Drive" never.
NAMELESS_AUTO_ACCEPT_MIN_WORDS = 3
NAMELESS_AUTO_ACCEPT_MIN_CHARS = 15
CLOSEST_AUTO_ACCEPT_SCORE_RANGE = (0.8, 1.0)
# Weight of the title in match_score() when the file has an artist (the
# artist's similarity gets the rest): a wrong artist spelling matters less
# than a different title.
FILE_MATCH_SCORE_TITLE_WEIGHT = 0.65

TITLE_AMPERSAND_WORDS = frozenset({"and", "und", "et", "i", "y", "n"})

# Finding duplicate songs (utils/database/song_duplicates.py): bracketed
# annotations that, besides TITLE_JUNK_SUFFIXES and featured-artist credits,
# don't make a song of their own ("Sorcererz (Visualiser)" is "Sorcererz").
# Only used to *propose* duplicates, never to rename anything.
DUPLICATE_TITLE_EXTRA_JUNK = frozenset({"visualiser", "visualizer", "official visualiser", "official visualizer",
                                        "teledysk", "oficjalny teledysk", "lyrics", "audio"})
# Words naming a version: a proposed duplicate whose titles differ in them is
# left unticked in the review ("Spider and the Fly" / "... (acoustic)").
DUPLICATE_VERSION_WORDS = frozenset({"remix", "mix", "live", "acoustic", "akustycznie", "cover", "version",
                                     "edit", "dub", "rework", "instrumental", "karaoke", "remaster", "remastered",
                                     "demo", "unplugged", "extended", "radio", "orchestral", "piano"})

# Download sites put their name into files and tags ("y2meta.com - ...",
# "(greenmp3.com)", "- dscom.it"): a word ending in one of these is dropped
# before a file name is split into artist and title, and from tags.
SITE_NAME_TLDS = ("com", "net", "org", "info", "biz", "io", "it", "pl", "ru")
# ... and the words listed in this file (src/config/), one per line
FILE_NAME_JUNK_FILE = "file_name_junk.txt"

TITLE_JUNK_SUFFIXES = [
    "official music video",
    "official lyric video",
    "official video",
    "official audio",
    "music video",
    "lyric video",
    "video clip",
    "videoclip",
    # Written as one word, e.g. Cody Lee's "...Green Onion(MusicVideo)"
    "musicvideo",
    "full song",
    # Upload quality tags, left at the end once everything else is cut
    # (real case: "MADUAR - Do It HD")
    "hd",
    "hq",
    "4k",
]
