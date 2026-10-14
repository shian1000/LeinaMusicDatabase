"""
Latin forms of names written in another alphabet (migration 0011):
songs.title_latin, artists.name_latin, albums.title_latin - "Кино" ->
"Kino". The owner writes them (song editor, Manage database -> "Fill in
Latin forms", or a reviewed field correction from Leina Radio, which shows
them as "Кино (Kino)"). Free text, no scheme imposed; nothing is stored
without the owner accepting it. App handoff:
docs/handoffs/latin-names-for-musicdatabaseapp.md.

Search and matching count a Latin form as one more name: an artist's
name_latin as a synonym (artist_match_synonyms() for code that takes the
synonyms text), a song's title_latin as another title. A one-song "Singles"
album copies its song's form (albums._link_single()), so it's never asked
for separately.
"""

from collections import Counter
from dataclasses import dataclass

from config.constants import ALBUM_TYPE_SINGLE
from utils.common.normalizer import normalize
from utils.common.romanize import suggest_latin
from utils.common.script_utils import has_non_latin
from utils.database.datatables import Album, Artist, Song

# model -> (name column, Latin form column)
LATIN_COLUMNS = {
    Song: ("title", "title_latin"),
    Artist: ("name", "name_latin"),
    Album: ("title", "title_latin"),
}

ARTIST, ALBUM, SONG = "artist", "album", "song"


def clean_latin(value) -> str | None:
    """As stored: trimmed, empty = NULL."""
    if value is None:
        return None
    return str(value).strip() or None


def name_of(obj) -> str | None:
    return getattr(obj, LATIN_COLUMNS[type(obj)][0])


def latin_of(obj) -> str | None:
    return getattr(obj, LATIN_COLUMNS[type(obj)][1], None)


def set_latin_form(obj, value) -> str | None:
    """Store `value` (cleaned) as the Latin form of a Song / Artist / Album;
    returns what was there. Caller commits."""
    old = latin_of(obj)
    setattr(obj, LATIN_COLUMNS[type(obj)][1], clean_latin(value))
    return old


def needs_latin_form(name: str | None) -> bool:
    """The name has a letter outside the Latin script - the phone shows a
    Latin form only for those."""
    return has_non_latin(name)


def with_latin_synonym(synonyms: str | None, name_latin: str | None) -> str | None:
    """An artists.synonyms text with `name_latin` as its first entry (unless
    already there), for matching code that takes the synonyms text."""
    name_latin = clean_latin(name_latin)
    if not name_latin:
        return synonyms
    existing = [s.strip() for s in (synonyms or "").split(",") if s.strip()]
    if normalize(name_latin) in {normalize(s) for s in existing}:
        return synonyms
    return ", ".join([name_latin, *existing])


def artist_match_synonyms(artist) -> str | None:
    """The artist's synonyms plus its Latin form - what matching (YouTube,
    fetchers, imports) should treat as its other names."""
    return with_latin_synonym(getattr(artist, "synonyms", None), getattr(artist, "name_latin", None))


def song_titles(song) -> list[str]:
    """The song's title and, if set, its Latin form."""
    return [t for t in (song.title, getattr(song, "title_latin", None)) if t]


def keep_latin_on_merge(keep, drop) -> None:
    """Merging two rows of one kind: the kept row's Latin form, else the
    dropped one's. Caller commits."""
    if not latin_of(keep) and latin_of(drop):
        set_latin_form(keep, latin_of(drop))


# ==================== what's still missing ====================

@dataclass
class LatinCandidate:
    """One row whose name has non-Latin letters and no Latin form yet."""
    kind: str  # ARTIST / ALBUM / SONG
    row: object
    context: str  # who it's by, for the list
    suggestion: str | None  # suggest_latin()'s offer; None = the owner types it

    @property
    def name(self) -> str:
        return name_of(self.row)


def _common_language(songs) -> str | None:
    languages = Counter(s.language for s in songs if s.language and s.language != "Unknown")
    return languages.most_common(1)[0][0] if languages else None


def suggest_latin_for(obj) -> str | None:
    """suggest_latin() for a Song / Artist / Album's name, with the language
    of its songs and its artist's origin to pick the Cyrillic system."""
    if isinstance(obj, Song):
        return suggest_latin(obj.title, obj.language, obj.artist.origin if obj.artist else None)
    if isinstance(obj, Artist):
        return suggest_latin(obj.name, _common_language(obj.songs), obj.origin)
    return suggest_latin(obj.title, _common_language(obj.songs), obj.artist.origin if obj.artist else None)


def missing_latin_forms(session, kinds=(ARTIST, ALBUM, SONG)) -> list[LatinCandidate]:
    """Rows with a non-Latin name and no Latin form, artists first, then
    albums, then songs, each by artist and name. Single albums are left out:
    they copy their song's form. Read-only."""
    found = []
    if ARTIST in kinds:
        for artist in session.query(Artist).filter(Artist.name_latin.is_(None)).order_by(Artist.name):
            if needs_latin_form(artist.name):
                found.append(LatinCandidate(ARTIST, artist, f"{len(artist.songs)} song(s)",
                                            suggest_latin_for(artist)))
    if ALBUM in kinds:
        albums = (session.query(Album)
                  .filter(Album.title_latin.is_(None))
                  .filter((Album.type.is_(None)) | (Album.type != ALBUM_TYPE_SINGLE))
                  .all())
        for album in sorted(albums, key=lambda a: ((a.artist.name if a.artist else "").lower(), a.title.lower())):
            if needs_latin_form(album.title) and album.songs:
                by = album.artist.name if album.artist else "various artists"
                found.append(LatinCandidate(ALBUM, album, by, suggest_latin_for(album)))
    if SONG in kinds:
        songs = session.query(Song).join(Artist).filter(Song.title_latin.is_(None)).order_by(Artist.name, Song.title)
        for song in songs:
            if needs_latin_form(song.title):
                found.append(LatinCandidate(SONG, song, song.artist.name, suggest_latin_for(song)))
    return found
