"""
Crediting a song to another artist row, as opposed to renaming the row.

An Artist row is shared by all its songs, so renaming it from one song's
editor renames it for every song. Here a song's songs.artist_id changes
instead: the song moves to an existing artist (or a new one) and the old
row keeps its other songs. songs.id never changes, so nothing in tag.db,
files.db or users' playlists has to follow; the album flush listener moves
songs.album_id because the change goes through `song.artist`.

The menus asking the questions live in utils/ui/artist_change.py.
"""

from utils.common.normalizer import normalize
from utils.database.albums import album_key, link_songs, reassign_artist_albums
from utils.database.datatables import Artist, Song
from utils.database.song_artists import (
    add_additional_artist,
    additional_artist_role,
    count_artist_songs,
    merge_artist_synonyms,
    reassign_additional_artist_links,
    remove_additional_artist,
)


def artists_named(session, name: str, exclude: Artist | None = None) -> list[Artist]:
    """Artists whose name or Latin form is `name` after normalize(), oldest
    first - an existing row to credit a song to rather than a second one
    spelt the same."""
    wanted = normalize(name or "")
    if not wanted:
        return []
    query = session.query(Artist).order_by(Artist.id)
    if exclude is not None and exclude.id is not None:
        query = query.filter(Artist.id != exclude.id)
    return [a for a in query.all()
            if wanted in (normalize(a.name or ""), normalize(a.name_latin or ""))]


def set_primary_artist(session, song: Song, artist: Artist, keep_old_as: str | None = None) -> None:
    """Credit `song` to `artist` instead of its current primary artist. If
    `artist` was one of the song's additional artists, that link goes (it
    can't be both). `keep_old_as` (a role, 'main'/'feat') keeps the old
    primary artist on the song as an additional one; None drops it. The old
    Artist row stays - see remove_unused_artist(). Caller commits."""
    old = song.artist
    if artist is old or (artist.id is not None and artist.id == song.artist_id):
        raise ValueError(f"'{artist.name}' is already the artist of this song")
    if additional_artist_role(song, artist):
        remove_additional_artist(song, artist)
    song.artist = artist
    # Flushed first: add_additional_artist() checks songs.artist_id too
    session.flush()
    if keep_old_as:
        add_additional_artist(song, old, keep_old_as)
        session.flush()
    _link_album_title(session, song)


def _link_album_title(session, song: Song) -> None:
    """An album title the two artists shared had no album row (left for
    "Resolve albums with several artists"); with one artist left now, the
    songs on it get one - the moved song and those it left behind."""
    key = album_key(song.album or "")
    if not key:
        return
    waiting = session.query(Song).filter(Song.album_id.is_(None), Song.album.isnot(None)).all()
    link_songs(session, [s for s in waiting if album_key(s.album) == key])
    session.flush()


def is_unused(session, artist: Artist) -> bool:
    """No song credits the artist any more, primary or additional."""
    return count_artist_songs(session, artist.id) == 0


def remove_unused_artist(session, artist: Artist, heir: Artist, keep_names: bool) -> None:
    """Delete `artist`, which no song credits any more (ValueError if one
    does). Album rows it still owns go to `heir`, so album ids stay stable.
    `keep_names` carries its name, synonyms and Latin form over to `heir` as
    synonyms (merge_artist_synonyms()) - for the same artist spelt
    differently. Caller commits."""
    if not is_unused(session, artist):
        raise ValueError(f"'{artist.name}' still has songs")
    reassign_artist_albums(session, artist.id, heir.id)
    if keep_names:
        merge_artist_synonyms(heir, artist)
    session.delete(artist)
    session.flush()


def swap_names_of_artist(song: Song) -> None:
    """Swap the song's title and its artist's name (and their Latin forms)
    in place. Renames the Artist row, so every other song of the artist gets
    the new name too. Caller commits."""
    artist = song.artist
    artist.name, song.title = song.title, artist.name
    artist.name_latin, song.title_latin = song.title_latin, artist.name_latin


def move_swapped_song(session, song: Song, artist: Artist | None = None) -> tuple[Artist, Artist | None]:
    """Fix a song stored as "Title - Artist" without renaming its artist row:
    the old artist name becomes the title (Latin forms too) and the song is
    credited to `artist`, an existing row named like the old title, or to a
    new one when None. The old artist is deleted when no song is left on it
    (its albums go to the new one). Returns (the song's artist now, the
    deleted old artist or None)."""
    old = song.artist
    if artist is None:
        artist = Artist(name=song.title, name_latin=song.title_latin)
        session.add(artist)
    song.title, song.title_latin = old.name, old.name_latin
    song.artist = artist
    session.flush()
    _link_album_title(session, song)
    if not is_unused(session, old):
        return artist, None
    # Nothing should point at it any more, but an extra-artist link may still
    reassign_additional_artist_links(session, old.id, artist.id)
    remove_unused_artist(session, old, artist, keep_names=False)
    return artist, old
