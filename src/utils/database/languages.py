"""
songs.language across the catalog. Every write is cleaned by Song's validator
(utils/common/language_names.clean_language()); rows stored before it, or
before a LANGUAGE_SYNONYMS entry was added, are brought to that spelling here
(Manage database -> "Unify language and country names", which does the same
for artists.origin - utils/common/origin_names.py). Which songs Fill missing data ->
Languages looks up, and the languages each artist's songs have, which it
checks a reading of the lyrics against.
"""

from collections import Counter

from sqlalchemy import func, or_

from config.constants import LANGUAGE_INSTRUMENTAL, LANGUAGE_SEPARATOR, LANGUAGE_UNKNOWN
from utils.common.language_names import clean_language
from utils.common.origin_names import clean_origin
from utils.database.datatables import Artist, Song
from utils.database.lyrics_store import is_instrumental


# The columns kept in one spelling by their model's validator, with what cleans them
SPELLINGS = {"language": (Song, Song.language, clean_language), "origin": (Artist, Artist.origin, clean_origin)}


def spelling_fixes(session, field: str) -> list[tuple[str, str | None, int]]:
    """(stored value, its clean spelling, rows) for every value of `field`
    ("language" / "origin") its cleaner changes, the most used first."""
    model, column, clean = SPELLINGS[field]
    counts = session.query(column, func.count(model.id)).filter(column.isnot(None)).group_by(column).all()
    fixes = [(value, clean(value), count) for value, count in counts]
    return sorted((fix for fix in fixes if fix[1] != fix[0]), key=lambda fix: (-fix[2], fix[0]))


def apply_spelling_fix(session, field: str, value: str) -> int:
    """Rewrite the rows stored with `value` in its clean spelling; returns
    how many. Caller commits."""
    model, column, _ = SPELLINGS[field]
    rows = session.query(model).filter(column == value).all()
    for row in rows:
        setattr(row, field, value)  # the validator stores the clean spelling
    return len(rows)


def has_language(song) -> bool:
    return song.language not in (None, "", LANGUAGE_UNKNOWN)


def other_song_languages(song) -> list[str]:
    """The stored languages of the song's artist's other songs."""
    return [other.language for other in getattr(song.artist, "songs", None) or []
            if other is not song and has_language(other) and other.language != LANGUAGE_INSTRUMENTAL]


def songs_missing_language(session) -> list[Song]:
    """Songs with no language or "Unknown", by artist then title; an
    instrumental version has none to find."""
    songs = (session.query(Song).join(Song.artist)
             .filter(or_(Song.language.is_(None), Song.language.in_(("", LANGUAGE_UNKNOWN))))
             .order_by(Artist.name, Song.title).all())
    return [song for song in songs if not is_instrumental(song)]


def artist_languages(session) -> dict[int, Counter]:
    """{artist id: Counter(language: songs)} of the songs with a language
    (not "Instrumental"); a song in two languages counts for both."""
    counts: dict[int, Counter] = {}
    rows = (session.query(Song.artist_id, Song.language)
            .filter(Song.language.isnot(None), Song.language.notin_(("", LANGUAGE_UNKNOWN, LANGUAGE_INSTRUMENTAL))))
    for artist_id, value in rows:
        counts.setdefault(artist_id, Counter())["songs"] += 1
        counts[artist_id].update(set(value.split(LANGUAGE_SEPARATOR)))
    return counts


def artist_share(counts: Counter | None, language: str) -> tuple[float, int]:
    """(share of the artist's songs with a language that have every one of
    `language`'s, how many songs with a language the artist has)."""
    songs = (counts or {}).get("songs", 0)
    if not songs:
        return 0.0, 0
    return min(counts[part] for part in language.split(LANGUAGE_SEPARATOR)) / songs, songs
