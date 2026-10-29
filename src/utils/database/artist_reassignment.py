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

from config.constants import ADDITIONAL_ARTIST_ROLE_FEAT, ADDITIONAL_ARTIST_ROLE_MAIN, COMPILATION_ARTIST_NAMES, TITLE_ARTISTS_SEPARATOR
from utils.common.normalizer import normalize
from utils.database.albums import album_key, link_songs, reassign_artist_albums
from utils.database.artist_splitting import propose_artist_split
from utils.database.datatables import Artist, Song
from utils.database.latin_names import artist_match_synonyms
from utils.database.song_artists import (
    add_additional_artist,
    additional_artist_role,
    count_artist_songs,
    merge_artist_synonyms,
    reassign_additional_artist_links,
    remove_additional_artist,
    song_all_artists,
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


# Artist names meaning "not known", matched whole after casefold(): "Unknown
# Mortal Orchestra" is a real artist
UNKNOWN_ARTIST_NAMES = ("", "unknown", "unknown artist")


def is_unknown_artist(artist: Artist) -> bool:
    return (artist.name or "").strip().casefold() in UNKNOWN_ARTIST_NAMES


def credit_unknown_artist_song(session, song: Song) -> Artist | None:
    """A song by an unknown artist whose title reads "Artist - Title": credit
    it to that artist (the oldest one named so, else a new row) and keep the
    rest as its title, both as written. The unknown artist's row is never
    renamed - it keeps its other songs. Returns the artist the song is
    credited to now, None when the song isn't one of these. Caller commits."""
    if not is_unknown_artist(song.artist) or " - " not in (song.title or ""):
        return None
    name, title = (part.strip() for part in song.title.split(" - ", 1))
    if not title or name.casefold() in UNKNOWN_ARTIST_NAMES:
        return None
    found = artists_named(session, name)
    artist = found[0] if found else Artist(name=name)
    if not found:
        session.add(artist)
    song.title = title
    set_primary_artist(session, song, artist)
    return artist


def _names_of(artist: Artist) -> list[str]:
    synonyms = [s.strip() for s in (artist_match_synonyms(artist) or "").split(",")]
    return [normalize(n) for n in (artist.name, *synonyms) if n and normalize(n)]


def _credited_as(song: Song, name: str) -> Artist | None:
    """The song's artist `name` stands for: its name or a synonym, or the
    surname of one ("Malenczuk" of "Maciej Maleńczuk" - not any word:
    "Orkiestra" of "Męskie Granie Orkiestra 2022" is a song's title)."""
    wanted = normalize(name)
    for artist in song_all_artists(song):
        for known in _names_of(artist):
            if wanted == known or (len(wanted) >= 3 and known.endswith(f" {wanted}")):
                return artist
    return None


def split_title_artists(session, song: Song) -> list[Artist] | None:
    """A title written "Artists / Title" ("Yugopolis & Malenczuk / Gdzie sa
    przyjaciele moi?"): the title becomes the part after " / ", and the names
    before it not credited yet are credited as main artists. Only when the
    names are the song's own artists - the song's main artist among them, or
    the song is a compilation's ("Various Artists": "Lao Che / Życie jest jak
    tramwaj", the first name becoming its artist) - and every other name is
    an artist already in the database, so two titles ("Old Love / New
    Love") are never taken apart. Returns the artists newly credited (the
    first one the new main artist of a compilation's song), None when the
    song isn't one of these. Caller commits."""
    prefix, separator, rest = (song.title or "").partition(TITLE_ARTISTS_SEPARATOR)
    rest = rest.strip()
    if not separator or not rest or not prefix.strip():
        return None
    split = propose_artist_split(prefix.strip())
    roles = ([(n, ADDITIONAL_ARTIST_ROLE_MAIN) for n in split.main] + [(n, ADDITIONAL_ARTIST_ROLE_FEAT) for n in split.feat]
             if split else [(prefix.strip(), ADDITIONAL_ARTIST_ROLE_MAIN)])
    names = [n for n, _role in roles]
    compilation = is_unknown_artist(song.artist) or normalize(song.artist.name or "") in COMPILATION_ARTIST_NAMES
    if not compilation and not any(_credited_as(song, n) is song.artist for n in names):
        return None
    new = []
    for name, role in roles:
        if _credited_as(song, name) and not compilation:
            continue
        found = artists_named(session, name)
        if not found:
            return None
        new.append((found[0], role))
    if compilation and (not new or new[0][1] != ADDITIONAL_ARTIST_ROLE_MAIN):
        return None
    song.title = rest
    if compilation:
        set_primary_artist(session, song, new[0][0])
    for artist, role in new[1:] if compilation else new:
        try:
            add_additional_artist(song, artist, role)
        except ValueError:
            pass  # already credited
    return [artist for artist, _role in new]


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
