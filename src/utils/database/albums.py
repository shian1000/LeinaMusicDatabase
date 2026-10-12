"""
Albums as rows (migrations 0005-0008): songs.album stays the free-text
album name - the MusicDatabaseApp phone client reads it, so it's always kept
filled - and songs.album_id points at the matching `albums` row.

The two are kept in step here, in one place, by a `before_flush` listener
(register_album_sync(), installed on music_db_manager's session factory):
whenever a song is added, or its `album` text or primary artist changes,
flushing the session finds or creates its album row. No code path that
writes songs.album has to remember to do it itself.

Rules (also in docs/data-model.md):
- Album rows are matched by normalize(title) and the album artist. Album ids
  are stable - the phone keys its cover cache on them - so rows are never
  renumbered; merging two albums keeps one of the ids (merge_albums()).
- A title only one main artist uses -> that artist's album, created on
  first use.
- SINGLES_ALBUM ("Singles") isn't an album: each such song gets a one-song
  album of its own, type ALBUM_TYPE_SINGLE, titled like the song.
- A title several main artists use is left for the user (album_id NULL)
  unless there's already a compilation (artist NULL) with that title, or the
  song's own artist already has that album. Manage database -> "Resolve
  albums with several artists" (utils/ui/album_resolution_review.py) settles
  them; unresolved = album_id NULL, so it's resumable.
- An album no song points at any more is kept, not deleted (its cover and id
  survive a typo fix and back).
Raw-SQL artist merges bypass the listener and must call
reassign_artist_albums() instead.
"""

import os
import weakref
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from sqlalchemy import event, inspect, or_, select

from config.constants import (
    ALBUM_COVERS_DIRNAME,
    ALBUM_COVER_SOURCE_FILE,
    ALBUM_COVER_STATUS_FOUND,
    ALBUM_TYPE_SINGLE,
    SINGLES_ALBUM,
)
from settings import Settings
from utils.common.debug import mlog
from utils.common.normalizer import normalize
from utils.database.datatables import Album, AlbumCover, Artist, Song

# Filename of the migration whose arrival triggers the one-off backfill of
# songs.album_id for the songs already in the database (see main.py)
ALBUM_ID_MIGRATION = "0006_song_album_id.sql"


def covers_dir() -> Path:
    """<database_dir>/covers - cover images, outside music.db and git."""
    return Path(str(Settings.database_dir)) / ALBUM_COVERS_DIRNAME


def cover_filename(album_id: int, source: str) -> str:
    """album_covers.path of a stored image: "<album_id>.jpg" for covers from
    the mp3 files, "<album_id>-<source>.jpg" for any other stored source."""
    return f"{album_id}.jpg" if source == ALBUM_COVER_SOURCE_FILE else f"{album_id}-{source}.jpg"


@lru_cache(maxsize=50_000)
def album_key(title: str | None) -> str:
    """What two album titles must share to be the same album."""
    text = (title or "").strip()
    return normalize(text) or text.lower()


def is_singles(album_text: str | None) -> bool:
    return (album_text or "").strip() == SINGLES_ALBUM


def _artist_key(obj):
    """The artist a Song (or Album) currently points at, including unflushed
    changes: the artist id, the Artist object while it has no id yet, or None
    (an album with no artist = various artists). Setting `artist_id` and
    setting `artist` both happen in this codebase; only the one that changed
    since the last flush is current."""
    state = inspect(obj)
    added = state.attrs.artist.history.added
    if added and added[0] is not None:
        return added[0].id if added[0].id is not None else added[0]
    if added and added[0] is None:
        return None
    if obj.artist_id is not None:
        return obj.artist_id
    if "artist" in state.dict and obj.artist is not None:
        return obj.artist.id if obj.artist.id is not None else obj.artist
    return None


def _set_album_artist(session, album: Album, key) -> None:
    """Always through the relationship: setting artist_id while `artist` is
    loaded would be undone by the flush."""
    if isinstance(key, int):
        # No autoflush: the backfill calls this thousands of times
        with session.no_autoflush:
            key = session.get(Artist, key)
    album.artist = key


def _attach(song: Song, album: Album) -> None:
    song.album_entry = album
    if album.year is None and song.year:
        album.year = song.year


def _most_common(values):
    values = [v for v in values if v]
    return Counter(values).most_common(1)[0][0] if values else None


class _AlbumIndex:
    """Non-single albums and not-yet-linked songs by album_key(), read once
    per flush (two light Core queries, not ORM loads of every album)."""

    def __init__(self, session):
        self.session = session
        self._albums = None
        self._pending = None

    def _load_albums(self):
        self._albums = defaultdict(list)
        rows = self.session.execute(
            select(Album.id, Album.title).where(
                or_(Album.type.is_(None), Album.type != ALBUM_TYPE_SINGLE)
            )
        )
        for album_id, title in rows:
            self._albums[album_key(title)].append(album_id)
        for obj in self.session.new:
            if isinstance(obj, Album) and obj.type != ALBUM_TYPE_SINGLE:
                self._albums[album_key(obj.title)].append(obj)

    def albums_for(self, key: str) -> list[Album]:
        if self._albums is None:
            self._load_albums()
        with self.session.no_autoflush:
            return [
                ref if isinstance(ref, Album) else self.session.get(Album, ref)
                for ref in self._albums.get(key, [])
            ]

    def album_of(self, key: str, artist_key) -> Album | None:
        return next((a for a in self.albums_for(key) if _artist_key(a) == artist_key), None)

    def add(self, key: str, album: Album) -> None:
        if self._albums is None:
            self._load_albums()
        if album not in self._albums[key]:
            self._albums[key].append(album)

    def pending_artists(self, key: str, exclude_ids: set) -> set:
        """Artist ids of songs (in the DB) titled `key` and still without an
        album - a multi-artist title waiting for the user."""
        if self._pending is None:
            self._pending = defaultdict(list)
            rows = self.session.execute(
                select(Song.id, Song.album, Song.artist_id).where(
                    Song.album_id.is_(None),
                    Song.album.isnot(None),
                    Song.album != "",
                    Song.album != SINGLES_ALBUM,
                )
            )
            for song_id, album, artist_id in rows:
                self._pending[album_key(album)].append((song_id, artist_id))
        return {artist_id for song_id, artist_id in self._pending.get(key, []) if song_id not in exclude_ids}


def _link_single(session, song: Song) -> None:
    """A "Singles" song gets its own one-song album, reused (and kept in step
    with the song) once it exists."""
    album = song.album_entry
    if album is None or album.type != ALBUM_TYPE_SINGLE:
        album = Album(type=ALBUM_TYPE_SINGLE)
        session.add(album)
        song.album_entry = album
    album.title = song.title
    album.year = song.year
    if _artist_key(album) != _artist_key(song):
        _set_album_artist(session, album, _artist_key(song))


def _link_songs(session, songs: list[Song], index: _AlbumIndex) -> list[Song]:
    """Find or create the album row of each song from its `album` text.
    Returns the songs left without one: titles several main artists share."""
    left = []
    by_key = defaultdict(list)
    for song in songs:
        text = (song.album or "").strip()
        if not text:
            song.album_entry = None
        elif is_singles(text):
            _link_single(session, song)
        else:
            by_key[album_key(text)].append(song)

    for key, group in by_key.items():
        albums = index.albums_for(key)
        compilation = next((a for a in albums if _artist_key(a) is None), None)
        if compilation is not None:
            for song in group:
                _attach(song, compilation)
            continue

        by_artist = {}
        for album in albums:
            by_artist.setdefault(_artist_key(album), album)
        remaining = []
        for song in group:
            album = by_artist.get(_artist_key(song))
            if album is not None:
                _attach(song, album)
            else:
                remaining.append(song)
        if not remaining:
            continue

        group_ids = {s.id for s in group if s.id is not None}
        artists = {_artist_key(s) for s in remaining}
        others = set(by_artist) | index.pending_artists(key, group_ids)
        if len(artists | others) == 1:
            album = Album(
                title=_most_common((s.album or "").strip() for s in remaining),
                year=_most_common(s.year for s in remaining),
            )
            session.add(album)
            _set_album_artist(session, album, artists.pop())
            index.add(key, album)
            for song in remaining:
                song.album_entry = album
        else:
            for song in remaining:
                song.album_entry = None
            left.extend(remaining)
    return left


def _keeps_current_album(song: Song, index: _AlbumIndex) -> bool:
    """True when the song's current album row still fits it. When only its
    artist changed (an artist split, a hand edit) and none of the album's
    other songs stays with the old artist, the album row moves to the new
    artist with it - same id, same cover."""
    current = song.album_entry
    text = (song.album or "").strip()
    if current is None or not text or is_singles(text) or current.type == ALBUM_TYPE_SINGLE:
        return False
    if album_key(current.title) != album_key(text):
        return False
    old_artist, new_artist = _artist_key(current), _artist_key(song)
    if old_artist is None or old_artist == new_artist:
        return True
    if index.album_of(album_key(text), new_artist) is not None:
        return False
    if any(s is not song and _artist_key(s) == old_artist for s in current.songs):
        return False
    _set_album_artist(inspect(song).session, current, new_artist)
    return True


_ALBUM_ATTRS = ("album", "artist_id", "artist")
_SINGLE_ATTRS = ("title", "year")


def _changed(song: Song, attrs) -> bool:
    state = inspect(song)
    return any(state.attrs[a].history.has_changes() for a in attrs)


def _before_flush(session, flush_context, instances) -> None:
    new_songs = [o for o in session.new if isinstance(o, Song)]
    dirty_songs = [o for o in session.dirty if isinstance(o, Song) and o not in session.deleted]
    changed = new_songs + [s for s in dirty_songs if _changed(s, _ALBUM_ATTRS)]
    for song in dirty_songs:
        if song not in changed and is_singles(song.album) and _changed(song, _SINGLE_ATTRS):
            changed.append(song)
    if not changed:
        return

    index = _AlbumIndex(session)
    to_link = [s for s in changed if not _keeps_current_album(s, index)]
    if to_link:
        _link_songs(session, to_link, index)


# Not event.contains(): see song_references._registered_factories
_registered_factories = weakref.WeakSet()


def register_album_sync(session_factory) -> None:
    """Keep songs.album_id in step with songs.album on every flush of
    sessions made by `session_factory` (a sessionmaker)."""
    if session_factory not in _registered_factories:
        event.listen(session_factory, "before_flush", _before_flush)
        _registered_factories.add(session_factory)


# --------------------
# Bulk linking (backfill) and the multi-artist titles left for the user
# --------------------

def link_unassigned_songs(session) -> list[Song]:
    """Link every song that has album text but no album row yet - the
    backfill after migration 0006, and safe to re-run. Titles several main
    artists share are grouped before deciding, so no artist gets that
    album ahead of the others. Returns the songs left for the user. Caller
    commits."""
    songs = (
        session.query(Song)
        .filter(Song.album_id.is_(None), Song.album.isnot(None), Song.album != "")
        .all()
    )
    return _link_songs(session, songs, _AlbumIndex(session))


def run_album_backfill() -> None:
    """Startup hook (main.py), run once right after migration 0006 lands."""
    from utils.database.music_db_manager import get_music_session

    session = get_music_session()
    try:
        left = link_unassigned_songs(session)
        session.commit()
        albums = session.query(Album).count()
        titles = len({album_key(s.album) for s in left})
        mlog(f"music.db: {albums} album(s) linked to their songs")
        if left:
            print(
                f"{len(left)} song(s) on {titles} album title(s) shared by several artists were left "
                f"without an album - Manage database -> \"Resolve albums with several artists\"."
            )
    finally:
        session.close()


@dataclass
class AlbumTitleGroup:
    """Every song under one album title that several main artists share."""
    key: str
    title: str
    songs: list = field(default_factory=list)    # pending + already linked, by artist then title
    pending: list = field(default_factory=list)  # album_id NULL

    @property
    def artist_ids(self) -> set:
        return {s.artist_id for s in self.songs}


def find_unresolved_album_groups(session, songs: list[Song] | None = None) -> list[AlbumTitleGroup]:
    """Album titles with songs left without an album row, with every other
    song on a (non-single) album of the same title, sorted by title. `songs`
    narrows it to those songs' titles (an import passes the songs it added)."""
    pending = (
        session.query(Song)
        .filter(
            Song.album_id.is_(None),
            Song.album.isnot(None),
            Song.album != "",
            Song.album != SINGLES_ALBUM,
        )
        .all()
    )
    wanted = None if songs is None else {album_key(s.album) for s in songs if s.album}
    groups = {}
    for song in pending:
        key = album_key(song.album)
        if wanted is not None and key not in wanted:
            continue
        groups.setdefault(key, AlbumTitleGroup(key=key, title=song.album.strip())).pending.append(song)

    index = _AlbumIndex(session)
    for key, group in groups.items():
        linked = [s for album in index.albums_for(key) for s in album.songs]
        group.songs = sorted(group.pending + linked, key=lambda s: (s.artist.name.lower(), s.title.lower()))
        group.title = _most_common((s.album or "").strip() for s in group.songs) or group.title
    return sorted(groups.values(), key=lambda g: g.title.lower())


def assign_songs_to_album(session, songs: list[Song], title: str, artist: Artist | None,
                          album_type: str | None = None) -> Album:
    """Put `songs` on one album (artist None = various artists). Reuses an
    album row of that title and artist, else one of the rows these songs are
    on now that they fill completely (keeps its id and cover), else creates
    one. Albums the move empties are merged into it. Caller commits."""
    key = album_key(title)
    index = _AlbumIndex(session)
    target_artist = artist.id if artist is not None else None
    song_set = set(songs)
    current = {s.album_entry for s in songs if s.album_entry is not None
               and s.album_entry.type != ALBUM_TYPE_SINGLE and album_key(s.album_entry.title) == key}

    album = index.album_of(key, target_artist)
    if album is None:
        absorbed = [a for a in current if set(a.songs) <= song_set]
        absorbed.sort(key=lambda a: (not a.covers, a.id or 0))
        album = absorbed[0] if absorbed else None
    if album is None:
        album = Album(title=_most_common((s.album or "").strip() for s in songs) or title,
                      year=_most_common(s.year for s in songs))
        session.add(album)
    _set_album_artist(session, album, target_artist)
    if album_type is not None:
        album.type = album_type

    for song in songs:
        _attach(song, album)
    session.flush()
    for old in current:
        if old is not album and not session.query(Song.id).filter(Song.album_id == old.id).first():
            merge_albums(session, album, old)
    return album


def unlink_songs(session, songs: list[Song]) -> None:
    """Take songs back off their album row (album text kept) - the resolver's
    starting point for splitting a title by hand."""
    for song in songs:
        song.album_entry = None


# --------------------
# Merging albums, artist merges
# --------------------

def merge_albums(session, keep: Album, drop: Album) -> None:
    """Move everything of `drop` onto `keep` and delete `drop`: its songs,
    any cover source `keep` lacks (the image file is renamed to keep's id),
    and external ids / year `keep` doesn't have. Caller commits."""
    for song in list(drop.songs):
        song.album_entry = keep
    for attr in ("year", "musicbrainz_release_group_id", "itunes_collection_id",
                 "deezer_album_id", "spotify_album_id"):
        if getattr(keep, attr) is None and getattr(drop, attr) is not None:
            setattr(keep, attr, getattr(drop, attr))

    kept_sources = {c.source for c in keep.covers if c.status == ALBUM_COVER_STATUS_FOUND}
    session.flush()
    for cover in list(drop.covers):
        if cover.source not in kept_sources and cover.status == ALBUM_COVER_STATUS_FOUND:
            for stale in [c for c in keep.covers if c.source == cover.source]:
                keep.covers.remove(stale)
            path = cover.path
            if path:
                new_path = cover_filename(keep.id, cover.source)
                src, dst = covers_dir() / path, covers_dir() / new_path
                if src.exists():
                    os.replace(src, dst)
                path = new_path
            keep.covers.append(AlbumCover(
                source=cover.source, url=cover.url, path=path, width=cover.width,
                height=cover.height, bytes=cover.bytes, status=cover.status,
                checked_at=cover.checked_at,
            ))
            kept_sources.add(cover.source)
        elif cover.path:
            stale_file = covers_dir() / cover.path
            if stale_file.exists():
                stale_file.unlink()
        drop.covers.remove(cover)
    session.flush()
    session.delete(drop)
    session.flush()


def reassign_artist_albums(session, from_id: int, to_id: int) -> None:
    """Point `from_id`'s albums at `to_id` when merging two artists. Run it
    *after* songs.artist_id has been reassigned (raw SQL, which the flush
    listener never sees). An album `to_id` already has under the same title
    absorbs the other one (merge_albums())."""
    session.flush()
    theirs = session.query(Album).filter(Album.artist_id == to_id).all()
    by_key = {album_key(a.title): a for a in theirs if a.type != ALBUM_TYPE_SINGLE}
    for album in session.query(Album).filter(Album.artist_id == from_id).all():
        target = by_key.get(album_key(album.title)) if album.type != ALBUM_TYPE_SINGLE else None
        if target is not None:
            merge_albums(session, target, album)
        else:
            _set_album_artist(session, album, to_id)
            if album.type != ALBUM_TYPE_SINGLE:
                by_key[album_key(album.title)] = album
    session.flush()
