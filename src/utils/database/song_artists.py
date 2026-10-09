"""
Multi-artist songs: the primary artist lives in songs.artist_id, any extra
ones in additional_song_artists (role 'main' for "A x B", 'feat' for
"A feat. B"). Single-artist songs have no rows there at all.

Everything that needs "all artists of a song" or "songs by this artist,
collabs included" should go through here instead of reading
Song.additional_artist_links or the table directly.
"""

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import aliased

from config.constants import (
    ADDITIONAL_ARTIST_ROLE_FEAT,
    ADDITIONAL_ARTIST_ROLE_MAIN,
    ADDITIONAL_ARTIST_ROLES,
    MULTI_ARTIST_FEAT_SEPARATOR,
    MULTI_ARTIST_MAIN_SEPARATOR,
)
from utils.database.datatables import AdditionalSongArtist, Artist, Song


def additional_artists(song: Song, role: str | None = None) -> list[Artist]:
    """The song's extra artists (optionally only one role), sorted by name -
    the table keeps no order, so this is what makes labels stable."""
    # getattr: song-like stand-ins (tests, plain namespaces) have no links
    links = getattr(song, "additional_artist_links", None) or []
    artists = [link.artist for link in links if role is None or link.role == role]
    return sorted(artists, key=lambda a: (a.name or "").lower())


def song_main_artists(song: Song) -> list[Artist]:
    """Primary artist first, then co-headliners."""
    return [song.artist, *additional_artists(song, ADDITIONAL_ARTIST_ROLE_MAIN)]


def song_feat_artists(song: Song) -> list[Artist]:
    return additional_artists(song, ADDITIONAL_ARTIST_ROLE_FEAT)


def song_all_artists(song: Song) -> list[Artist]:
    return [*song_main_artists(song), *song_feat_artists(song)]


def song_artist_label(song: Song) -> str:
    """Display/search string for a song's artists, e.g. "A x B feat. C".
    For a single-artist song this is exactly song.artist.name."""
    label = MULTI_ARTIST_MAIN_SEPARATOR.join(a.name for a in song_main_artists(song))
    feats = song_feat_artists(song)
    if feats:
        label += MULTI_ARTIST_FEAT_SEPARATOR + ", ".join(a.name for a in feats)
    return label


def song_artist_credits(song: Song) -> dict | None:
    """JSON-friendly credits for YouTube matching (score_result()'s
    main_artists/feat_artists, stored as-is in the playlist cache):
    {"main": [[name, synonyms], ...], "feat": [[name, synonyms], ...]}, or
    None for a single-artist song so it keeps the old matching path."""
    if not additional_artists(song):
        return None
    return {
        "main": [[a.name, a.synonyms] for a in song_main_artists(song)],
        "feat": [[a.name, a.synonyms] for a in song_feat_artists(song)],
    }


def song_main_artist_label(song: Song) -> str:
    """"A x B" - main artists only, no feat. What YouTube is queried with."""
    return MULTI_ARTIST_MAIN_SEPARATOR.join(a.name for a in song_main_artists(song))


def additional_artist_name_contains(word: str):
    """SQL filter: song has an additional artist whose name contains `word`.
    Uses an aliased Artist because song searches already join the primary
    Artist - an unaliased subquery would correlate to that outer row instead."""
    extra = aliased(Artist)
    return Song.id.in_(
        select(AdditionalSongArtist.song_id)
        .join(extra, extra.id == AdditionalSongArtist.artist_id)
        .where(func.lower(extra.name).contains(word.lower()))
    )


def song_has_artist_id(artist_id: int):
    """SQL filter: artist is the song's primary or one of its additional artists."""
    return or_(
        Song.artist_id == artist_id,
        Song.id.in_(
            select(AdditionalSongArtist.song_id).where(AdditionalSongArtist.artist_id == artist_id)
        ),
    )


def count_artist_songs(session, artist_id: int) -> int:
    """Songs credited to the artist in any way (primary or additional)."""
    return session.query(Song).filter(song_has_artist_id(artist_id)).count()


def reassign_additional_artist_links(session, from_id: int, to_id: int) -> None:
    """Point every additional_song_artists row of `from_id` at `to_id` when
    merging two artists. Run it *after* songs.artist_id has been reassigned:
    links that would collide with an existing (song, to_id) row, or that
    would list the song's own primary artist as an extra one, are dropped."""
    # Flush first so expiring the link collections below can't discard
    # pending ORM changes to them
    session.flush()
    params = {"from_id": from_id, "to_id": to_id}
    session.execute(
        text(
            "DELETE FROM additional_song_artists WHERE artist_id = :from_id AND ("
            " song_id IN (SELECT song_id FROM additional_song_artists WHERE artist_id = :to_id)"
            " OR song_id IN (SELECT id FROM songs WHERE artist_id = :to_id))"
        ),
        params,
    )
    session.execute(
        text("UPDATE additional_song_artists SET artist_id = :to_id WHERE artist_id = :from_id"),
        params,
    )
    # A song whose primary artist just became to_id may already list to_id as extra
    session.execute(
        text(
            "DELETE FROM additional_song_artists WHERE artist_id = :to_id"
            " AND song_id IN (SELECT id FROM songs WHERE artist_id = :to_id)"
        ),
        params,
    )
    # Raw SQL bypasses the ORM - drop cached link collections only. Not
    # expire_all(): callers loop over Artist objects they then delete by raw
    # SQL, and an expired deleted row raises on its next attribute access.
    for obj in list(session.identity_map.values()):
        if isinstance(obj, Song):
            session.expire(obj, ["additional_artist_links"])
        elif isinstance(obj, Artist):
            session.expire(obj, ["additional_song_links"])


def _find_link(song: Song, artist: Artist) -> AdditionalSongArtist | None:
    for link in song.additional_artist_links:
        if link.artist is artist or (artist.id is not None and link.artist_id == artist.id):
            return link
    return None


def _check_role(role: str) -> None:
    if role not in ADDITIONAL_ARTIST_ROLES:
        raise ValueError(f"Unknown role '{role}', expected one of {ADDITIONAL_ARTIST_ROLES}")


def add_additional_artist(song: Song, artist: Artist, role: str) -> None:
    """Credit `artist` as an extra artist of `song`. Raises ValueError if it's
    already the primary artist, already linked, or the role is unknown.
    Caller commits."""
    _check_role(role)
    if song.artist is artist or (artist.id is not None and song.artist_id == artist.id):
        raise ValueError(f"'{artist.name}' is already the primary artist of this song")
    if _find_link(song, artist):
        raise ValueError(f"'{artist.name}' is already an additional artist of this song")
    song.additional_artist_links.append(AdditionalSongArtist(artist=artist, role=role))


def remove_additional_artist(song: Song, artist: Artist) -> None:
    """Drop `artist` from the song's extra artists (the Artist row itself
    stays). Raises ValueError if it isn't one. Caller commits."""
    link = _find_link(song, artist)
    if link is None:
        raise ValueError(f"'{artist.name}' is not an additional artist of this song")
    song.additional_artist_links.remove(link)


def set_additional_artist_role(song: Song, artist: Artist, role: str) -> None:
    """Switch an extra artist between 'main' and 'feat'. Caller commits."""
    _check_role(role)
    link = _find_link(song, artist)
    if link is None:
        raise ValueError(f"'{artist.name}' is not an additional artist of this song")
    link.role = role


def additional_artist_role(song: Song, artist: Artist) -> str | None:
    link = _find_link(song, artist)
    return link.role if link else None
