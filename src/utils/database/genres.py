"""
A song's genre(s): songs.genre (migration 0013), read by Leina Radio's song
radio (app handoff docs/handoffs/musicdatabase-genres.md, our answer
docs/handoffs/genres-for-musicdatabaseapp.md).

Stored as text: the genres comma-separated, the main one first
("indie pop, synth-pop"), NULL = none known. No commas inside a genre name.

One spelling per genre across the database matters more than anything:
the phone counts two songs as sharing a genre only when the names are equal
(ignoring case, diacritics and punctuation, but not spaces - "synthpop" and
"synth-pop" differ to it). So every value goes through clean_genres() on
the way in - the editor, Song actions, Leina Radio's field corrections and
the MusicBrainz lookup alike. A genre is looked up by genre_key() (no case,
diacritics, punctuation or spaces, "&" = "and") in, in order:
GENRE_SYNONYMS, the spellings music.db already uses, MusicBrainz's genre list
(src/config/musicbrainz_genres.txt); an unknown genre is kept lowercased as
typed.
"""

from functools import lru_cache
from pathlib import Path

from config.constants import GENRE_SYNONYMS, GENRE_VOCABULARY_FILE
from utils.common.normalizer import normalize
from utils.database.datatables import Artist, Song

SEPARATORS = (",", ";")


def genre_key(name: str | None) -> str:
    """"Synth-pop", "synth pop" and "Synthpop" -> "synthpop";
    "Rock & Roll" -> "rockandroll"."""
    return normalize((name or "").replace("&", " and ")).replace(" ", "")


@lru_cache(maxsize=1)
def vocabulary() -> dict:
    """{genre_key: MusicBrainz's spelling} of every genre MusicBrainz knows."""
    path = Path(__file__).resolve().parents[2] / "config" / GENRE_VOCABULARY_FILE
    known = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return known
    for line in lines:
        name = line.strip()
        if name:
            known.setdefault(genre_key(name), name)
    return known


def is_known_genre(name: str) -> bool:
    """A genre MusicBrainz knows (or a listed synonym) - for telling genres
    from the other folksonomy tags ("seen live", "favourite")."""
    key = genre_key(name)
    return key in GENRE_SYNONYMS or key in vocabulary()


def split_genres(value) -> list[str]:
    """The genres of a stored or typed value, as written (trimmed, empty
    parts dropped)."""
    if value is None:
        return []
    text = str(value)
    for separator in SEPARATORS[1:]:
        text = text.replace(separator, SEPARATORS[0])
    return [part.strip() for part in text.split(SEPARATORS[0]) if part.strip()]


def canonical_genre(name: str, known: dict | None = None) -> str:
    """The spelling one genre is stored under. `known` = {genre_key:
    spelling} already in music.db (known_genre_spellings())."""
    key = genre_key(name)
    if key in GENRE_SYNONYMS:
        return GENRE_SYNONYMS[key]
    if known and key in known:
        return known[key]
    if key in vocabulary():
        return vocabulary()[key]
    return " ".join(name.split()).lower()


def clean_genres(value, known: dict | None = None) -> str | None:
    """As stored: each genre in its one spelling, the order kept, repeats
    dropped, joined by ", "; nothing = None."""
    genres, seen = [], set()
    for name in split_genres(value):
        genre = canonical_genre(name, known)
        if genre_key(genre) and genre_key(genre) not in seen:
            seen.add(genre_key(genre))
            genres.append(genre)
    return ", ".join(genres) or None


def known_genre_spellings(session, exclude: Song | None = None) -> dict:
    """{genre_key: spelling} of every genre music.db's songs already have
    (the most used spelling, should two ever differ)."""
    counts: dict = {}
    query = session.query(Song.genre).filter(Song.genre.isnot(None), Song.genre != "")
    if exclude is not None and exclude.id is not None:
        query = query.filter(Song.id != exclude.id)
    for (value,) in query:
        for name in split_genres(value):
            spellings = counts.setdefault(genre_key(name), {})
            spellings[name] = spellings.get(name, 0) + 1
    return {key: max(spellings, key=spellings.get) for key, spellings in counts.items() if key}


def set_song_genre(session, song: Song, value, known: dict | None = None) -> str | None:
    """Store `value` (clean_genres()d against the database's spellings) as
    the song's genre; returns what was there. Caller commits."""
    if known is None:
        known = known_genre_spellings(session, exclude=song)
    old = song.genre
    song.genre = clean_genres(value, known)
    return old


def keep_genre_on_merge(keep: Song, drop: Song) -> None:
    """Merging two songs: the kept song's genre, else the dropped one's.
    Caller commits."""
    if not keep.genre and drop.genre:
        keep.genre = drop.genre


def songs_missing_genre(session) -> list[Song]:
    """Songs with no genre yet, by artist then title."""
    return (session.query(Song).join(Song.artist)
            .filter((Song.genre.is_(None)) | (Song.genre == ""))
            .order_by(Artist.name, Song.title).all())
