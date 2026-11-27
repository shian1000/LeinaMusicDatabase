from dataclasses import dataclass, field
from typing import Callable, Optional

from config.constants import GENRE_LEVEL_SONG


@dataclass
class AlbumTrack:
    """One track of a release's tracklist. track_number / disc_number None =
    unknown (disc None also means a one-disc release, as in songs.disc_number);
    artist is the track's credit, None when the module can't tell."""
    title: str
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    artist: Optional[str] = None


@dataclass
class DiscoveryResult:
    """What a discovery module found, plus what it actually matched.

    Modules that can identify which title/artist they matched against should
    report them here so discoveries_manager can independently verify the
    match instead of trusting the module's own (possibly buggy) matching
    logic. Leave a field None if the module has no reliable way to report it.

    track_number / disc_number: the matched song's place on that album.
    load_tracks: the album's whole tracklist, fetched only when called - it
    may cost a request, so the manager asks only when other songs of the
    artist still wait for an album (see discoveries_manager.AlbumTrackCache).

    needs_review marks an album that's only a guess (Google names the first
    release it knows the recording from, maybe a compilation): the other
    modules are still asked, and the owner confirms it if none of them knows.

    What else the page or answer the album came from says, so nobody asks
    the site again for it (None / empty = it doesn't say):
    year: that release's year - the album's, a single's own; kept only from
    a module enabled for Years too (fill_missing_albums.py).
    album_type: ALBUM_TYPE_ALBUM / _EP / _SOUNDTRACK only - never single or
    compilation, see constants.DISCOVERY_ALBUM_TYPES.
    album_ids: the release's id on the site, by its `albums` column
    (constants.ALBUM_EXTERNAL_ID_COLUMNS).
    release_kind: the kind of version the release itself is (constants.
    VERSION_LIVE for a live album: MusicBrainz's "Live" release group) - a
    live version takes only a live release's album and year unasked
    (version_names.lookup_fits_version()).
    """
    album: str
    matched_title: Optional[str] = None
    matched_artist: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    load_tracks: Optional[Callable[[], list[AlbumTrack]]] = None
    needs_review: bool = False
    year: Optional[int] = None
    album_type: Optional[str] = None
    album_ids: dict = field(default_factory=dict)
    release_kind: Optional[str] = None


@dataclass
class YearDiscoveryResult:
    """Same contract as DiscoveryResult, for a module's get_release_year().

    matched_title is whatever release/recording title the module actually
    found the year on (an album title when the query was an album, a
    recording title when the query was a single's song title) - never an
    echo of the query, for the same reason as DiscoveryResult.

    needs_review marks a year that's only a guess (e.g. a YouTube upload
    date) - the caller shows it to the user instead of writing it straight
    away. source_url is what to show them alongside it. release_kind: as
    DiscoveryResult's, of the release the year is of.
    """
    year: int
    matched_title: Optional[str] = None
    matched_artist: Optional[str] = None
    needs_review: bool = False
    source_url: Optional[str] = None
    release_kind: Optional[str] = None


@dataclass
class LyricsDiscoveryResult:
    """One candidate a module's get_lyrics() found. A module returns a list
    of them (the manager picks, by title, artist and length - see
    discoveries_manager.discover_lyrics()), or one, or None.

    plain: the text, lines separated by "\\n"; synced: LRC ("[01:23.45] line");
    instrumental: the source says the song has no words. matched_* and
    duration_s are what the source says the text belongs to - never an echo
    of the query. from_song_file: read from one of the song's own library
    files (already matched to it), so nothing needs checking. source_ref:
    the source's own id of the text, if it has one.
    """
    plain: Optional[str] = None
    synced: Optional[str] = None
    instrumental: bool = False
    matched_title: Optional[str] = None
    matched_artist: Optional[str] = None
    duration_s: Optional[float] = None
    source_ref: Optional[str] = None
    from_song_file: bool = False


@dataclass
class LanguageDiscoveryResult:
    """What a module's get_language() found: the language as stored
    (language_names.clean_language(), "eng / jpn", LANGUAGE_INSTRUMENTAL
    "zxx" for a song without words). confidence: how sure a guess is (0-1, a
    reading of the lyrics), None when the source says it outright. matched_*
    are what the source says the language belongs to - never an echo of the
    query; from_song: read from the song's own stored data, nothing to check.
    needs_origin: a Serbian / Croatian / Bosnian reading neither the artist's
    country nor the year settles (language_names.yugoslav_language()) - not
    saved until the artist's origin is known, or the owner types it.
    never_sure: a source too often wrong to save without the owner
    (YouTube's audio language), however it reads."""
    language: str
    matched_title: Optional[str] = None
    matched_artist: Optional[str] = None
    confidence: Optional[float] = None
    from_song: bool = False
    source_ref: Optional[str] = None
    needs_origin: bool = False
    never_sure: bool = False


@dataclass
class GenreDiscoveryResult:
    """What a module's get_genre() found: the genres as the source names them,
    the main one first (discover_genre() keeps those MusicBrainz's genre list
    knows, in the database's spelling). level: what they're the genres of
    (GENRE_LEVEL_SONG / _ALBUM / _ARTIST - an artist's are never sure).
    matched_* are what the source says the genres belong to - never an echo
    of the query; checked: the module matched the song itself (MusicBrainz's
    own title / artist checks), nothing to check again. never_sure: a source
    the owner reviews before anything is saved."""
    genres: list[str]
    level: str = GENRE_LEVEL_SONG
    matched_title: Optional[str] = None
    matched_artist: Optional[str] = None
    source_ref: Optional[str] = None
    checked: bool = False
    never_sure: bool = False
