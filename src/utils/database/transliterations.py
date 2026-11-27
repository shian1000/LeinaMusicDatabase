"""
Latin forms of names per transliteration method (music.db's
name_transliterations, migration 0027): "Океан Ельзи" -> uk-national "Okean
Elzy", uk-slavic "Okean Eľzy". Keyed by (table_name, row_id, column_name,
method), so artists.name, songs.title and albums.title fit; only artists'
names are filled so far. App handoff:
docs/handoffs/name-transliterations-for-musicdatabaseapp.md.

The automatic rows (source "romanize") are worked out again by
refresh_artist_transliterations() before every copy for Leina Radio
(sharing.share_databases()) and from Manage database -> Resolve duplicates ->
"Resolve duplicated additional names of artists": romanize.py's system for
the name (the artist's songs' language, origin and letters decide, as for the
name_latin suggestions), and for a Ukrainian name also the owner's Slavic
alphabet. A name romanize can't read whole (kanji the dictionary lacks) or
reads only by guessed vowels (Arabic) gets none. The owner's rows (source
"owner") are never touched. A rename drops the automatic rows at once and a
deletion all of them (the migration's triggers); a merge first carries the
owner's over (keep_transliterations_on_merge()).

artists.name_latin stays the owner's display form; these count only as more
names: search and matching take them like synonyms
(latin_names.artist_match_synonyms()).

Duplicate additional names: duplicate_synonyms() finds the artists.synonyms
entries that repeat the name, name_latin, a transliteration or an earlier
synonym - exactly (dropped without asking) or only after normalize() (the
owner ticks which go: it may be the only record of an official spelling).
"""

import unicodedata
from dataclasses import dataclass

from config.constants import (
    TRANSLITERATION_METHODS,
    TRANSLITERATION_SOURCE_OWNER,
    TRANSLITERATION_SOURCE_ROMANIZE,
    TRANSLITERATION_UK_SLAVIC,
)
from utils.common.artist_credits import split_synonyms
from utils.common.debug import mlog
from utils.common.normalizer import composed, normalize
from utils.common.romanize import _only_latin_letters, latin_system, suggest_latin, ukrainian_slavic
from utils.database.datatables import Artist, NameTransliteration
from utils.database.latin_names import _latin_args, needs_latin_form


def worked_out_forms(name: str | None, language: str | None = None, origin: str | None = None) -> dict:
    """{method: Latin form} romanize gives `name` - its system's, plus the
    Slavic alphabet for Ukrainian; {} for a Latin name or one it can't read
    (Arabic's vowels are a guess: none)."""
    system = latin_system(name, language, origin)
    if not system or system[0] not in TRANSLITERATION_METHODS:
        return {}
    forms = {TRANSLITERATION_METHODS[system[0]]: suggest_latin(name, language, origin)}
    if system[0] == "uk":
        slavic = " ".join(ukrainian_slavic(unicodedata.normalize("NFKC", name)).split())
        if slavic and _only_latin_letters(slavic):
            forms[TRANSLITERATION_UK_SLAVIC] = slavic
    return {method: composed(value) for method, value in forms.items()}


def refresh_artist_transliterations(session) -> tuple[int, int, int]:
    """Bring the automatic rows of every artist's name up to date: added,
    changed, removed (counts). Idempotent; the owner's rows stay, and a method
    the owner wrote gets no automatic row. Caller commits."""
    rows = {(t.row_id, t.method): t for t in session.query(NameTransliteration)
            .filter(NameTransliteration.table_name == "artists", NameTransliteration.column_name == "name")}
    wanted = {}
    artists = session.query(Artist).order_by(Artist.id).all()
    for artist in artists:
        if needs_latin_form(artist.name):
            for method, value in worked_out_forms(*_latin_args(artist)).items():
                wanted[(artist.id, method)] = value
    added = changed = removed = 0
    for key, row in rows.items():
        if row.source == TRANSLITERATION_SOURCE_ROMANIZE and key not in wanted:
            session.delete(row)
            removed += 1
    for (artist_id, method), value in wanted.items():
        row = rows.get((artist_id, method))
        if row is None:
            session.add(NameTransliteration(table_name="artists", row_id=artist_id, column_name="name",
                                            method=method, value=value, source=TRANSLITERATION_SOURCE_ROMANIZE))
            added += 1
        elif row.source == TRANSLITERATION_SOURCE_ROMANIZE and row.value != value:
            row.value = value
            changed += 1
    session.flush()
    for artist in artists:  # Artist.transliterations is view-only: loaded again with the new rows
        session.expire(artist, ["transliterations"])
    if added or changed or removed:
        mlog(f"Artists' transliterations: {added} added, {changed} changed, {removed} removed")
    return added, changed, removed


def keep_transliterations_on_merge(session, keep, drop, column: str = "name") -> None:
    """Merging two rows of one table: `drop`'s owner-written forms go to
    `keep` for the methods `keep` has no owner-written one of (its automatic
    one of that method goes); the rest go with `drop` (the delete trigger).
    Caller commits."""
    if session is None or keep.id is None or drop.id is None:
        return
    table = type(keep).__tablename__
    query = session.query(NameTransliteration).filter(NameTransliteration.table_name == table,
                                                      NameTransliteration.column_name == column)
    kept = {t.method: t for t in query.filter(NameTransliteration.row_id == keep.id)}
    for row in query.filter(NameTransliteration.row_id == drop.id,
                            NameTransliteration.source == TRANSLITERATION_SOURCE_OWNER).all():
        if row.method in kept and kept[row.method].source == TRANSLITERATION_SOURCE_OWNER:
            continue
        if row.method in kept:
            session.delete(kept[row.method])
            session.flush()
        row.row_id = keep.id
    session.flush()


# ==================== duplicate additional names ====================

@dataclass
class SynonymDuplicate:
    """One artists.synonyms entry that repeats another name of the artist."""
    artist: Artist
    synonym: str
    of: str  # what it repeats, as the list says it: "its name", "uk-slavic", ...
    exact: bool  # the same text (else equal only after normalize())
    other: str  # the name it repeats


def duplicate_synonyms(session) -> list[SynonymDuplicate]:
    """Every synonym of every artist that repeats its name, its name_latin,
    one of its transliterations or an earlier synonym of it - by artist name.
    Read-only."""
    found = []
    for artist in session.query(Artist).filter(Artist.synonyms.isnot(None)).order_by(Artist.name):
        names = [("its name", artist.name), ("its Latin form", artist.name_latin),
                 *((t.method, t.value) for t in artist.transliterations)]
        names = [(of, name) for of, name in names if name]
        for synonym in split_synonyms(artist.synonyms):
            same = next(((of, name) for of, name in names if name.strip() == synonym), None)
            alike = same or next(((of, name) for of, name in names
                                  if normalize(name) and normalize(name) == normalize(synonym)), None)
            if alike:
                found.append(SynonymDuplicate(artist, synonym, alike[0], same is not None, alike[1]))
            else:
                names.append(("another additional name", synonym))
    return found


def remove_synonyms(duplicates) -> int:
    """Drop these entries from their artists' synonyms (written as they are,
    not through set_artist_synonyms(), which would drop every near repeat
    the owner kept too). Returns how many went. Caller commits."""
    by_artist = {}
    for duplicate in duplicates:
        by_artist.setdefault(duplicate.artist.id, (duplicate.artist, []))[1].append(duplicate.synonym)
    removed = 0
    for artist, synonyms in by_artist.values():
        kept = split_synonyms(artist.synonyms)
        for synonym in synonyms:
            if synonym in kept:
                kept.remove(synonym)
                removed += 1
        artist.synonyms = ", ".join(kept) or None
    return removed
