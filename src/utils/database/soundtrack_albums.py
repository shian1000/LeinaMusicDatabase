"""Soundtrack albums in the catalog: which game or film an artist name may
really be ("Shin Megami Tensei III: Nocturne Music"), and the spellings of one
soundtrack to bring to the one form, "<work> OST". Asked about in
utils/ui/soundtrack_review.py; nothing here commits.

The works list (soundtrack_works, migration 0025; Manage database ->
"Soundtrack works"): films, games, anime a song gets credited to with no
soundtrack word in the name - "Nana - Kuroi Namida" is Anna Tsuchiya's song.
A listed name is a work whole, however short; one only read from an album
("Rio OST") needs two words, or "Rio Reiser" would read as it. The matchers
find a song credited to a work among every artist's songs whose album names it
(import_engine.LocalSongMatcher.match_work()) - a work of an "<work> OST" album
of two words or more too, so the list is only needed for the rest.
A soundtrack the owner confirms goes on the list (list_album_work()): its
album brought to "<work> OST" in "Resolve soundtrack album names", or saved
from the Albums step's soundtrack guesses."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from config.constants import ALBUM_TYPE_SINGLE, ALBUM_TYPE_SOUNDTRACK, SINGLES_ALBUM
from utils.common.normalizer import normalize
from utils.common.project_names import split_names
from utils.common.soundtrack_names import soundtrack_album, soundtrack_work, weak_soundtrack_work, work_key
from utils.database.albums import unify_album_titles
from utils.database.datatables import Album, Song, SoundtrackWork

# A shorter work would read too many artists as it: "Rio OST" and "Rio Reiser"
_MIN_WORDS_OF_KNOWN_WORK = 2


def known_soundtrack_works(session) -> dict[str, str]:
    """The works the catalog has an "<work> OST" album of, and the listed
    ones (their other spellings too), by normalize()."""
    rows = session.query(Album.title).filter(Album.type.is_(None) | (Album.type != ALBUM_TYPE_SINGLE))
    works = (soundtrack_work(title) for title, in rows)
    return {**{normalize(work): work for work in works if work and normalize(work)}, **listed_works(session)}


def guess_soundtrack_album(artist_name: str, works: dict[str, str], listed: dict[str, str] | None = None) -> str | None:
    """The soundtrack album an artist name may stand for: it is a listed
    work (`listed`, listed_works()), its ending says so ("... Music"), or it
    is a work the catalog knows, whole or at its start ("Persona 1 (PSP)" -
    "Persona 1"; the longest such work). A guess: the owner confirms it."""
    name = normalize(artist_name)
    if listed and name in listed:
        return soundtrack_album(listed[name])
    if work := weak_soundtrack_work(artist_name):
        return soundtrack_album(works.get(normalize(work), work))
    known = [key for key in works
             if len(key.split()) >= _MIN_WORDS_OF_KNOWN_WORK and (name == key or name.startswith(f"{key} "))]
    return soundtrack_album(works[max(known, key=len)]) if known else None


# ==================== The works list ====================

def work_spellings(work: SoundtrackWork) -> tuple[str, ...]:
    return (work.name, *split_names(work.aliases))


def listed_works(session) -> dict[str, str]:
    """normalize(spelling) -> the work's name, for every spelling of every
    listed work."""
    return {normalize(spelling): work.name for work in session.query(SoundtrackWork).all()
            for spelling in work_spellings(work) if normalize(spelling)}


def matching_works(session) -> dict[str, str]:
    """The works an artist name may be whole, for the matchers: the listed
    ones, and those of "<work> OST" albums named in two words or more - no
    row needed for "Cave Story" once "Cave Story OST" is in the catalog."""
    derived = {key: work for key, work in known_soundtrack_works(session).items()
               if len(key.split()) >= _MIN_WORDS_OF_KNOWN_WORK}
    return {**derived, **listed_works(session)}


def credited_work(artist_name: str | None, works: dict[str, str]) -> str | None:
    """The work an artist name is - one of `works` (listed_works() or
    matching_works()), or one ending in a soundtrack word ("Tekken 5 OST" ->
    "Tekken 5") - or None."""
    return works.get(normalize(artist_name or "")) or soundtrack_work(artist_name)


def soundtrack_work_named(session, name: str | None) -> SoundtrackWork | None:
    """The listed work `name` is the name or another spelling of."""
    key = normalize(name or "")
    if not key:
        return None
    return next((w for w in session.query(SoundtrackWork).all() if key in map(normalize, work_spellings(w))), None)


def add_soundtrack_work(session, name: str, aliases: str | None = None) -> SoundtrackWork:
    """A new listed work, or ValueError when the name is another's spelling.
    The caller commits."""
    name = name.strip()
    if not normalize(name):
        raise ValueError("A work needs a name")
    if soundtrack_work_named(session, name) is not None:
        raise ValueError(f"'{name}' is on the list already")
    work = SoundtrackWork(name=name, aliases=aliases or None)
    session.add(work)
    session.flush()
    return work


def list_album_work(session, album_titles: list[str]) -> SoundtrackWork | None:
    """Put the work of a soundtrack the owner confirmed on the list: the
    work of the first title ("Rick and Morty OST" -> "Rick and Morty"), the
    other titles' works ("Rick adn Morty") as its other spellings. A work
    listed already gets the new spellings; None when no title names a
    work. The caller commits."""
    works = []
    for title in album_titles:
        work = soundtrack_work((title or "").strip())
        if work and normalize(work) not in map(normalize, works):
            works.append(work)
    if not works:
        return None
    listed = next((found for work in works if (found := soundtrack_work_named(session, work))), None)
    if listed is None:
        return add_soundtrack_work(session, works[0], ", ".join(works[1:]))
    known = set(map(normalize, work_spellings(listed)))
    new = [work for work in works if normalize(work) not in known]
    if new:
        listed.aliases = ", ".join([*split_names(listed.aliases), *new])
    return listed


@dataclass
class SoundtrackVariants:
    """One soundtrack's songs, under titles that aren't all its one form."""
    title: str
    songs: list = field(default_factory=list)

    @property
    def titles(self) -> list[str]:
        return sorted({song.album.strip() for song in self.songs})


def _best_spelling(works: list[str]) -> str:
    """The most used; on a tie one not written in capitals."""
    counts = Counter(works)
    return max(counts, key=lambda work: (counts[work], not work.isupper()))


def find_soundtrack_variants(session) -> list[SoundtrackVariants]:
    """Soundtracks with a title other than "<work> OST", those spelt several
    ways first."""
    songs = session.query(Song).filter(Song.album.isnot(None), Song.album != "", Song.album != SINGLES_ALBUM)
    groups = defaultdict(list)
    for song in songs:
        work = soundtrack_work(song.album.strip())
        if work:
            groups[work_key(work)].append((work, song))

    found = []
    for members in groups.values():
        title = soundtrack_album(_best_spelling([work for work, _ in members]))
        variants = SoundtrackVariants(title, [song for _, song in members])
        if variants.titles != [title]:
            found.append(variants)
    return sorted(found, key=lambda v: (len(v.titles) == 1, v.title.lower()))


def apply_soundtrack_variants(session, variants: SoundtrackVariants) -> Album:
    """Put the songs on one album of that title, marked a soundtrack."""
    return unify_album_titles(session, variants.songs, variants.title, ALBUM_TYPE_SOUNDTRACK)
