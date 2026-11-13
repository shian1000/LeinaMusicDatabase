from dataclasses import dataclass
from typing import Callable, Optional


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
    """
    album: str
    matched_title: Optional[str] = None
    matched_artist: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    load_tracks: Optional[Callable[[], list[AlbumTrack]]] = None
    needs_review: bool = False


@dataclass
class YearDiscoveryResult:
    """Same contract as DiscoveryResult, for a module's get_release_year().

    matched_title is whatever release/recording title the module actually
    found the year on (an album title when the query was an album, a
    recording title when the query was a single's song title) - never an
    echo of the query, for the same reason as DiscoveryResult.

    needs_review marks a year that's only a guess (e.g. a YouTube upload
    date) - the caller shows it to the user instead of writing it straight
    away. source_url is what to show them alongside it.
    """
    year: int
    matched_title: Optional[str] = None
    matched_artist: Optional[str] = None
    needs_review: bool = False
    source_url: Optional[str] = None


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
