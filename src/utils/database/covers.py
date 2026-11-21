"""songs.covered_artist_id (migration 0020): the artist a song is a cover of.
The title loses the credit and the song is shown "Performer - Title (Original
cover)" (cover_names.title_with_cover()). Reading a credit out of a title:
utils/common/cover_names.py; the import does it in sanitize's "cover_credit"
step, the songs already stored are offered in a list (find_covers(),
utils/ui/cover_review.py). An artist only covered keeps its row
(artist_reassignment.is_unused()), merges follow through
song_artists.reassign_additional_artist_links().

songs.cover_translated (migration 0026): 1 = a translated cover, with words of
its own in another language than the original's (Mistmorn's Ukrainian "Дарма"
of American Football's "Never Meant"). Its lyrics are never looked up as the
original's (discoveries_manager._lyrics_queries()), nor its language read from
them; a cover that isn't translated takes its original's language
(languages.original_language(), original_song() here).
"""
from dataclasses import dataclass

from sqlalchemy.orm import object_session

from utils.common.cover_names import read_cover
from utils.common.normalizer import normalize
from utils.common.script_utils import matching_key
from utils.database.datatables import Artist, Song
from utils.database.latin_names import song_titles
from utils.database.song_artists import song_has_artist_id
from utils.common.artist_credits import split_synonyms


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
        for spelling in filter(None, [artist.name_latin, *split_synonyms(artist.synonyms)]):
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
    for song in session.query(Song).filter(Song.covered_artist_id.is_(None), Song.covered_title.is_(None)).order_by(Song.id):
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
    """`artist` None: not a cover of an artist. ValueError for a song's own
    artist. The caller commits."""
    if artist is not None and any(song.artist_id == artist.id for song in songs):
        raise ValueError(f"'{artist.name}' is the artist of one of these songs")
    for song in songs:
        song.covered_artist = artist
        _drop_translated_of_non_cover(song)


def set_covered_title(songs: list[Song], title: str | None) -> None:
    """The original's title (None: not known). The caller commits."""
    for song in songs:
        song.covered_title = title
        _drop_translated_of_non_cover(song)


def is_marked_cover(song) -> bool:
    """Marked as a cover: a covered artist or the original's title is stored."""
    return bool(getattr(song, "covered_artist", None) is not None or getattr(song, "covered_artist_id", None)
                or getattr(song, "covered_title", None))


def is_translated(song) -> bool:
    return bool(getattr(song, "cover_translated", None))


def _drop_translated_of_non_cover(song) -> None:
    if not is_marked_cover(song):
        song.cover_translated = None


def set_cover_translated(songs: list[Song], translated: bool) -> None:
    """Mark covers as translated (words of their own in another language than
    the original's) or not. ValueError, with nothing changed, for a song not
    marked as a cover. The caller commits."""
    if translated and not all(is_marked_cover(song) for song in songs):
        raise ValueError("only a cover can be a translated cover - set its covered artist or title first")
    for song in songs:
        song.cover_translated = 1 if translated else None


def _title_keys(titles) -> set[str]:
    """Spelling-free keys of titles, across alphabets ("Kroŭ by vada" is "Кроў бы вада")."""
    return {key for title in titles if title for key in (normalize(title), matching_key(title)) if key}


def original_song(song, session=None) -> Song | None:
    """The original of a cover of an artist, when music.db has it: a song of
    the covered artist titled like the cover's covered_title or its own title,
    not a cover itself; a plain song before a version of it. Reads only."""
    covered_id = getattr(song, "covered_artist_id", None)
    session = session or object_session(song)
    if covered_id is None or session is None:
        return None
    keys = _title_keys([song.covered_title, song.title])
    candidates = session.query(Song).filter(song_has_artist_id(covered_id), Song.id != song.id,
                                            Song.covered_artist_id.is_(None), Song.covered_title.is_(None))
    matches = [candidate for candidate in candidates if keys & _title_keys(song_titles(candidate))]
    return min(matches, key=lambda candidate: (candidate.version_kind is not None, candidate.id), default=None)


def is_known_work(session, name: str) -> bool:
    """Some song already names `name` as the original's title, with no artist:
    "(Bella Ciao cover)" read from a file is that song, not an artist."""
    key = normalize(name)
    return any(normalize(title) == key for (title,) in session.query(Song.covered_title).filter(
        Song.covered_title.isnot(None), Song.covered_artist_id.is_(None)))


def mark_cover_of(session, song: Song, name: str) -> None:
    """Mark `song` from a name read in a title. The caller commits."""
    if is_known_work(session, name):
        set_covered_title([song], name)
    else:
        set_covered_artist([song], artist_named(session, name))


def name_cover(session, song: Song, name: str) -> None:
    """Mark a song whose title only says "cover" (find_covers()'s unnamed) as
    a cover of `name`, cutting the credit out of its title. ValueError, with
    nothing changed, for the song's own artist. The caller commits."""
    from utils.discoveries.import_engine import find_matching_artist

    artist = find_matching_artist(name, session)
    if artist is not None and artist.id == song.artist_id:
        raise ValueError(f"'{artist.name}' is the artist of this song")
    reading = read_cover(song.artist.name, song.title)
    title = reading.title if reading and reading.title else song.title
    apply_cover_proposal(session, CoverProposal(song, title, name.strip(), artist))


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
    if proposal.performer:
        performer = artist_named(session, proposal.performer)
        if performer.id != song.artist_id:
            set_primary_artist(session, song, performer)
    if proposal.covered:
        set_covered_artist([song], proposal.covered)
    else:
        mark_cover_of(session, song, proposal.covered_name)
    song.title = proposal.title
    session.flush()
