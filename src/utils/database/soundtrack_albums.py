"""Soundtrack albums in the catalog: which game or film an artist name may
really be ("Shin Megami Tensei III: Nocturne Music"), and the spellings of one
soundtrack to bring to the one form, "<work> OST". Asked about in
utils/ui/soundtrack_review.py; nothing here commits."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from config.constants import ALBUM_TYPE_SINGLE, ALBUM_TYPE_SOUNDTRACK, SINGLES_ALBUM
from utils.common.normalizer import normalize
from utils.common.soundtrack_names import soundtrack_album, soundtrack_work, weak_soundtrack_work, work_key
from utils.database.albums import unify_album_titles
from utils.database.datatables import Album, Song

# A shorter work would read too many artists as it: "Rio OST" and "Rio Reiser"
_MIN_WORDS_OF_KNOWN_WORK = 2


def known_soundtrack_works(session) -> dict[str, str]:
    """The works the catalog has an "<work> OST" album of, by normalize()."""
    rows = session.query(Album.title).filter(Album.type.is_(None) | (Album.type != ALBUM_TYPE_SINGLE))
    works = (soundtrack_work(title) for title, in rows)
    return {normalize(work): work for work in works if work and normalize(work)}


def guess_soundtrack_album(artist_name: str, works: dict[str, str]) -> str | None:
    """The soundtrack album an artist name may stand for: its ending says so
    ("... Music"), or it is a work the catalog knows, whole or at its start
    ("Persona 1 (PSP)" - "Persona 1"; the longest such work). A guess: the
    owner confirms it."""
    if work := weak_soundtrack_work(artist_name):
        return soundtrack_album(works.get(normalize(work), work))
    name = normalize(artist_name)
    known = [key for key in works
             if len(key.split()) >= _MIN_WORDS_OF_KNOWN_WORK and (name == key or name.startswith(f"{key} "))]
    return soundtrack_album(works[max(known, key=len)]) if known else None


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
