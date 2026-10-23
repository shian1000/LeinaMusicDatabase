"""songs.kind (migration 0014): what kind of recording a song row is.

NULL is an ordinary song. SONG_KIND_FULL_ALBUM marks a row that is really a
whole album played through - its YouTube link is the full-album video, its
mp3 the whole album. It stays a song row because files.db, playlists, tags
and bad_youtube_videos all point at songs.id; only the label differs.

A full album is filed under the album it plays: marking one whose album is
unknown (or the "Singles" sentinel) sets its album to its own title (without
"(Full Album)"), and utils.database.albums links the album row from that text.

Matching a file / an imported entry to a song (import_engine) keeps the two
apart: an entry that doesn't look like a whole album (no marker in its title,
shorter than FULL_ALBUM_MIN_DURATION_S or of unknown length) never matches a
full album - a track "Migration" must not be taken for the album "Migration".
One that does look like it tries full albums first, then songs (a long file
may be a mix the owner keeps as a song).
"""
import json
import re

from config.constants import (
    FULL_ALBUM_IGNORE_FILE,
    FULL_ALBUM_MIN_DURATION_S,
    FULL_ALBUM_TITLE_MARKERS,
    SONG_KIND_FULL_ALBUM,
    SONG_KIND_LABELS,
    SONG_KINDS,
)
from utils.common.normalizer import normalize
from utils.database.albums import is_singles
from utils.database.datatables import Song

_IGNORE_PATH = FULL_ALBUM_IGNORE_FILE

_NORMALIZED_MARKERS = sorted({normalize(m) for m in FULL_ALBUM_TITLE_MARKERS}, key=len, reverse=True)
# Raw-title forms, for cutting a marker out: longest first, so "full album
# stream" goes whole rather than leaving "stream"
_RAW_MARKERS = "|".join(re.escape(m) for m in sorted(FULL_ALBUM_TITLE_MARKERS, key=len, reverse=True))
_OPEN, _CLOSE = r"\(\[\{【（「", r"\)\]\}】）」"
_BRACKETED_MARKER = re.compile(
    rf"\s*[{_OPEN}][^{_CLOSE}]*?(?:{_RAW_MARKERS})[^{_CLOSE}]*[{_CLOSE}]", re.IGNORECASE)
_TRAILING_MARKER = re.compile(rf"\s*[-–—|:.,]*\s*(?:{_RAW_MARKERS})\b.*$", re.IGNORECASE)
_LEFTOVER_SEPARATORS = " -–—|:.,"


def is_full_album(song: Song) -> bool:
    return song.kind == SONG_KIND_FULL_ALBUM


def kind_label(song: Song) -> str:
    return SONG_KIND_LABELS.get(song.kind, song.kind)


def title_has_full_album_marker(title: str | None) -> bool:
    """Does the title say it's a whole album ("Migration (Full Album)",
    "Legenda - cały album")? Diacritics and case ignored; a marker glued to
    the next word still counts ("(Full AlbumEp)")."""
    key = normalize(title or "")
    return any(re.search(rf"(?:^| ){re.escape(m)}", key) for m in _NORMALIZED_MARKERS)


def strip_full_album_marker(title: str) -> str:
    """The title without its full-album words: "Migration (Full Album)." ->
    "Migration", "Legenda (1991) - cały album" -> "Legenda (1991)". The
    title itself when that would leave nothing."""
    stripped = _BRACKETED_MARKER.sub("", title)
    stripped = _TRAILING_MARKER.sub("", stripped)
    if stripped != title:
        stripped = stripped.strip().rstrip(_LEFTOVER_SEPARATORS).strip()
    return stripped or title


def looks_like_full_album(title: str | None, duration_s: int | None = None) -> bool:
    """An entry (a file, a video) that may be a whole album: a marker in its
    title, or at least FULL_ALBUM_MIN_DURATION_S long."""
    return title_has_full_album_marker(title) or (duration_s or 0) >= FULL_ALBUM_MIN_DURATION_S


def songs_for_entry(songs: list, title: str | None, duration_s: int | None = None) -> list:
    """The songs an entry may be matched to, in the order to try them: for
    an entry that looks like a whole album, full albums (and rows whose own
    title says they are one) first, then the rest; otherwise every song but
    the full albums."""
    if not looks_like_full_album(title, duration_s):
        return [s for s in songs if not is_full_album(s)]
    albums = [s for s in songs if is_full_album(s) or title_has_full_album_marker(s.title)]
    return albums + [s for s in songs if s not in albums]


def set_song_kind(songs: list[Song], kind: str | None) -> list[Song]:
    """Set `kind` on the songs (None = an ordinary song). Returns the songs
    whose album text was filled in from their title. The caller commits."""
    if kind is not None and kind not in SONG_KINDS:
        raise ValueError(f"Unknown song kind '{kind}'")
    filed = []
    for song in songs:
        song.kind = kind
        if kind == SONG_KIND_FULL_ALBUM and (not song.album or is_singles(song.album)):
            song.album = strip_full_album_marker(song.title)
            filed.append(song)
    return filed


def full_album_filter():
    """SQLAlchemy filter for full-album rows."""
    return Song.kind == SONG_KIND_FULL_ALBUM


def load_full_album_ignore_list() -> set[int]:
    """Song ids the owner said aren't full albums."""
    try:
        return set(json.loads(_IGNORE_PATH.read_text(encoding="utf-8")))
    except (FileNotFoundError, ValueError):
        return set()


def add_to_full_album_ignore_list(song_ids: list[int]) -> None:
    ignored = load_full_album_ignore_list() | set(song_ids)
    _IGNORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _IGNORE_PATH.write_text(json.dumps(sorted(ignored)), encoding="utf-8")
