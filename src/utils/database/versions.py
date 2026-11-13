"""songs.version_kind / version_label / version_of (migration 0022): a song row
that is a version of another - a live recording, a remix, an acoustic take.
It stays a row of its own (own id, video, plays, album: two recordings are
never one), titled without the marker and shown with it
(version_names.title_with_version()): "Dziwny jest ten świat" (live, of #3994)
is "Dziwny jest ten świat (Live)". version_of points at the original when
music.db has it - always the original itself, never another version.

Reading a marker out of a title: utils/common/version_names.py. A new song
gets it when created (import_engine.create_song_entry(), like a full album's
marker); the songs stored before are offered in a list (find_versions(),
utils/ui/version_review.py). Matching keeps versions and originals apart
(song_kinds.songs_for_entry()). Merges move the versions over
(song_duplicates.merge_songs()), a deleted original leaves them without one
(song_references' flush listener).
"""
from dataclasses import dataclass

from config.constants import VERSION_KINDS
from utils.common.normalizer import normalize
from utils.common.version_names import read_version
from utils.database.datatables import Song
from utils.database.song_kinds import sure_version


@dataclass
class VersionProposal:
    """A stored song whose title says it's a version: `title` without the
    marker, `original` the song it's a version of when the artist has exactly
    one of that title. `certain`: a marker naming a clear kind (not just
    "version", song_kinds.sure_version())."""
    song: Song
    title: str
    kind: str
    label: str | None = None
    original: Song | None = None
    certain: bool = False


def original_of(session, song: Song, title: str) -> Song | None:
    """The one song of `song`'s artist titled `title` (normalized) that isn't a
    version, of the same kind and project, or None when there's none or more."""
    key = normalize(title)
    found = [s for s in session.query(Song).filter(Song.artist_id == song.artist_id, Song.id != song.id,
                                                   Song.version_kind.is_(None))
             if normalize(s.title) == key and s.kind == song.kind and s.project_id == song.project_id]
    return found[0] if len(found) == 1 else None


def find_versions(session, skip_ids=frozenset()) -> list[VersionProposal]:
    """The stored songs, not versions yet and not in `skip_ids` (looked at
    before), whose titles say they are one; by artist. Reads only."""
    proposals = []
    for song in session.query(Song).filter(Song.version_kind.is_(None)).order_by(Song.id):
        if song.id in skip_ids or (reading := read_version(song.title)) is None:
            continue
        proposals.append(VersionProposal(song, reading.title, reading.kind, reading.label,
                                         original_of(session, song, reading.title), sure_version(reading)))
    return sorted(proposals, key=lambda p: ((p.song.artist.name or "").lower(), p.title.lower()))


def set_version(songs: list[Song], kind: str | None, label: str | None = None, original: Song | None = None) -> None:
    """Mark the songs as versions of `kind` (None: not a version - label and
    original go too), shown with `label` (None: the kind's own), of `original`
    (an original's version points at the original itself). ValueError, with
    nothing changed, for an unknown kind or a song as its own original. The
    caller commits."""
    if kind is not None and kind not in VERSION_KINDS:
        raise ValueError(f"Unknown version kind '{kind}'")
    if original is not None and original.version_of is not None:
        original = original.original or original
    if original is not None and any(song.id == original.id for song in songs):
        raise ValueError(f"'{original.title}' can't be a version of itself")
    for song in songs:
        song.version_kind = kind
        song.version_label = (label or None) if kind else None
        song.version_of = original.id if kind and original is not None else None


def apply_version_proposal(session, proposal: VersionProposal) -> None:
    """Mark the song and cut the marker out of its title. The caller commits."""
    set_version([proposal.song], proposal.kind, proposal.label, proposal.original)
    proposal.song.title = proposal.title
    session.flush()


def mark_new_song_version(session, song: Song) -> None:
    """A song just created whose title says it's a version: marked, the marker
    cut, its original found among the artist's songs. The caller commits."""
    # A plain "version" is left to "Find versions": it may be the title itself
    if (reading := read_version(song.title)) is None or not sure_version(reading):
        return
    set_version([song], reading.kind, reading.label, original_of(session, song, reading.title))
    song.title = reading.title


def versions_of(session, song: Song) -> list[Song]:
    """The original's versions (`song` being the original or one of them),
    the original first, `song` itself left out."""
    root = song.original or song if song.version_of else song
    rows = session.query(Song).filter(Song.version_of == root.id).order_by(Song.id).all()
    return [s for s in [root, *rows] if s.id != song.id]


def move_versions(session, from_song_id: int, to_song_id: int) -> None:
    """A merge: the versions of the dropped song become the kept one's (and the
    kept song, if it was one of them, stops being a version of itself). The
    caller commits."""
    for song in session.query(Song).filter(Song.version_of == from_song_id):
        song.version_of = None if song.id == to_song_id else to_song_id
    # The kept song took the dropped one's original, which was itself
    if (kept := session.get(Song, to_song_id)) is not None and kept.version_of == to_song_id:
        kept.version_of = None
