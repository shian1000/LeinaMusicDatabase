"""songs.covered_artist_id (migration 0020): the artist a song is a cover of.
The title loses the credit and the song is shown "Performer - Title (Original
cover)" (cover_names.title_with_cover()). Reading a credit out of a title:
utils/common/cover_names.py; the import does it in sanitize's "cover_credit"
step, the songs already stored are offered in a list (find_covers(),
utils/ui/cover_review.py). An artist only covered keeps its row
(artist_reassignment.is_unused()), merges follow through
song_artists.reassign_additional_artist_links().
"""
from dataclasses import dataclass

from utils.common.cover_names import read_cover
from utils.common.normalizer import normalize
from utils.database.datatables import Artist, Song


@dataclass
class CoverProposal:
    """A stored song whose title names the artist it covers. `covered`: that
    artist's row, None for a name the database doesn't have yet. `certain`:
    the title's name is exactly a stored artist's. `performer`: who plays it,
    when the stored artist turned out to be the original."""
    song: Song
    title: str
    covered_name: str
    covered: Artist | None = None
    performer: str | None = None
    certain: bool = False
    note: str | None = None


def _artists_by_key(session) -> dict:
    """normalize()d name, Latin form and every synonym -> Artist; a name
    comes before another artist's synonym."""
    artists = session.query(Artist).order_by(Artist.id).all()
    keys = {}
    for artist in artists:
        spellings = [artist.name_latin, *(artist.synonyms or "").split(",")]
        for spelling in spellings:
            if spelling and spelling.strip():
                keys.setdefault(normalize(spelling), artist)
    keys.update({normalize(artist.name): artist for artist in artists if artist.name})
    keys.pop("", None)
    return keys


def _artists_of_title(session) -> dict:
    """normalize()d title -> the primary artist ids of the songs named so."""
    titles = {}
    for artist_id, title in session.query(Song.artist_id, Song.title):
        titles.setdefault(normalize(title or ""), set()).add(artist_id)
    return titles


def find_covers(session, skip_ids=frozenset()) -> tuple[list[CoverProposal], list[Song]]:
    """Which stored songs are covers, from their titles: (the ones naming or
    implying the original artist, by artist; those only saying "cover", for
    the owner to name). `skip_ids`: songs already looked at. Reads only."""
    from utils.discoveries.import_engine import find_matching_artist

    by_key = _artists_by_key(session)
    titles = _artists_of_title(session)

    def known(name: str) -> str | None:
        artist = by_key.get(normalize(name))
        return artist.name if artist else None

    proposals, unnamed = [], []
    for song in session.query(Song).filter(Song.covered_artist_id.is_(None)).order_by(Song.id):
        if song.id in skip_ids:
            continue
        reading = read_cover(song.artist.name, song.title, known)
        if reading is None:
            continue
        if reading.covered is None or (not reading.performer and normalize(reading.covered) == normalize(song.artist.name)):
            others = titles.get(normalize(reading.title), set()) - {song.artist_id}
            original = session.get(Artist, next(iter(others))) if len(others) == 1 and reading.credit_read else None
            if original is None:
                unnamed.append(song)
            else:
                proposals.append(CoverProposal(song, reading.title, original.name, original,
                                               note="another artist has this title"))
            continue
        artist = by_key.get(normalize(reading.covered))
        certain = artist is not None
        if artist is None:
            artist = find_matching_artist(reading.covered, session)
        if artist is not None and artist.id == song.artist_id and not reading.performer:
            unnamed.append(song)
            continue
        note = None if artist else "new artist"
        proposals.append(CoverProposal(song, reading.title, artist.name if artist else reading.covered, artist,
                                       reading.performer, certain and bool(reading.title), note))
    return (sorted(proposals, key=lambda p: (p.covered_name.lower(), p.title.lower())),
            sorted(unnamed, key=lambda s: (s.artist.name.lower(), s.title.lower())))


def set_covered_artist(songs: list[Song], artist: Artist | None) -> None:
    """`artist` None: not a cover. ValueError for a song's own artist. The
    caller commits."""
    if artist is not None and any(song.artist_id == artist.id for song in songs):
        raise ValueError(f"'{artist.name}' is the artist of one of these songs")
    for song in songs:
        song.covered_artist = artist


def artist_named(session, name: str) -> Artist:
    """The artist of that name, a new row when there's none."""
    from utils.discoveries.import_engine import find_matching_artist

    artist = find_matching_artist(name, session)
    if artist is None:
        artist = Artist(name=name.strip())
        session.add(artist)
        session.flush()
    return artist


def apply_cover_proposal(session, proposal: CoverProposal) -> None:
    """Mark the song, cut the credit out of its title and, for "Cover by X",
    credit it to X (never renaming the shared row). The original artist stays
    as the covered one. The caller commits."""
    from utils.database.artist_reassignment import set_primary_artist

    song = proposal.song
    covered = proposal.covered or artist_named(session, proposal.covered_name)
    if proposal.performer:
        performer = artist_named(session, proposal.performer)
        if performer.id != song.artist_id:
            set_primary_artist(session, song, performer)
    set_covered_artist([song], covered)
    song.title = proposal.title
    session.flush()
