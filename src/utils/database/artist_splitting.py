"""
Splitting a joined artist name ("Sw@da x Maxim", "A feat. B, C") into
separate artists linked through additional_song_artists - see
song_artists.py. Used by Manage database -> "Split joined artist names" and
at the end of every import (run_import_batch()), always behind one batch
review (utils/ui/artist_split_review.py), since a separator character alone
can't tell "Sw@da & Maxim" from "Simon & Garfunkel".
"""

import json
import re
from dataclasses import dataclass

from config.constants import (
    ADDITIONAL_ARTIST_ROLE_FEAT,
    ADDITIONAL_ARTIST_ROLE_MAIN,
    ARTIST_SPLIT_IGNORE_FILE,
)
from utils.common.artist_credits import (
    JOINING_WORD_SEPARATOR,
    UPPERCASE_X_SEPARATOR,
    SplitProposal,
    propose_artist_split,
)
from utils.common.normalizer import normalize
from utils.database.albums import link_songs, reassign_artist_albums
from utils.database.datatables import Artist, Song
from utils.database.latin_names import artist_match_synonyms
from utils.database.song_artists import add_additional_artist

_IGNORE_PATH = ARTIST_SPLIT_IGNORE_FILE

# ---- ignore list ------------------------------------------------------------

def load_split_ignore_list() -> set[str]:
    try:
        return set(json.loads(_IGNORE_PATH.read_text(encoding="utf-8")))
    except (FileNotFoundError, ValueError):
        return set()


def add_to_split_ignore_list(names: list[str]) -> None:
    ignored = load_split_ignore_list() | {normalize(n) for n in names}
    _IGNORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _IGNORE_PATH.write_text(json.dumps(sorted(ignored), ensure_ascii=False, indent=2), encoding="utf-8")


# ---- finding and applying ---------------------------------------------------

def _artists_by_name(session) -> dict[str, Artist]:
    """normalized name/synonym -> artist; a real name beats a synonym, the
    lowest id wins a tie."""
    by_name, by_synonym = {}, {}
    for artist in session.query(Artist).order_by(Artist.id).all():
        by_name.setdefault(normalize(artist.name), artist)
        for syn in (artist_match_synonyms(artist) or "").split(","):
            if syn.strip():
                by_synonym.setdefault(normalize(syn), artist)
    return {**by_synonym, **by_name}


@dataclass
class SplitCandidate:
    artist: Artist
    proposal: SplitProposal
    existing: dict[str, Artist]  # proposal name -> artist already in the DB
    song_count: int

    @property
    def preselected(self) -> bool:
        # Every part already being its own artist is as strong as "feat."/" x "
        return self.proposal.confident or len(self.existing) == len(self.proposal.names)


def _propose_split_on_existing_parts(
    artist: Artist, by_name: dict[str, Artist], separator: re.Pattern, require_all: bool
) -> SplitProposal | None:
    """A name joined by `separator`, as a split proposal only when its parts
    are already different artists in the DB - every part (`require_all`) or at
    least one. See UPPERCASE_X_SEPARATOR / JOINING_WORD_SEPARATOR."""
    if not separator.search(artist.name):
        return None
    proposal = propose_artist_split(separator.sub(" x ", artist.name))
    if proposal is None:
        return None
    # "Tom Petty and the Heartbreakers", "Louis Jordan and His Tympany Five" -
    # a band name; "Between the Buried and Me" - "Me" isn't the artist "Me"
    if any(n.lower().startswith(("the ", "his ", "her ")) or len(n) <= 2 for n in proposal.names):
        return None
    found = [by_name.get(normalize(n)) for n in proposal.names]
    if any(match is not None and match.id == artist.id for match in found):
        return None
    if not any(found) or (require_all and not all(found)):
        return None
    proposal.original = artist.name
    proposal.confident = False  # " x " was ours; the existing parts decide
    return proposal


def find_split_candidates(session, artists: list[Artist] | None = None) -> list[SplitCandidate]:
    """Joined-looking artists (all of them, or just `artists`), minus the
    ignore list and anything without songs to move."""
    ignored = load_split_ignore_list()
    by_name = _artists_by_name(session)
    pool = artists if artists is not None else session.query(Artist).order_by(Artist.name).all()

    candidates = []
    for artist in pool:
        if normalize(artist.name) in ignored:
            continue
        proposal = (
            propose_artist_split(artist.name)
            or _propose_split_on_existing_parts(artist, by_name, UPPERCASE_X_SEPARATOR, require_all=True)
            or _propose_split_on_existing_parts(artist, by_name, JOINING_WORD_SEPARATOR, require_all=False)
        )
        if proposal is None:
            continue
        song_count = len(artist.songs) + len(artist.additional_song_links)
        if not song_count:
            continue
        existing = {
            n: by_name[normalize(n)]
            for n in proposal.names
            if normalize(n) in by_name and by_name[normalize(n)].id != artist.id
        }
        candidates.append(SplitCandidate(artist, proposal, existing, song_count))
    return candidates


def _add_quietly(song: Song, artist: Artist, role: str) -> None:
    try:
        add_additional_artist(song, artist, role)
    except ValueError:
        pass  # already the primary or already linked - nothing to add


def apply_artist_split(session, candidate: SplitCandidate, created: dict[str, Artist] | None = None) -> list[Artist]:
    """Move every song of the joined artist onto its parts: first main name
    becomes songs.artist_id, the rest additional_song_artists rows. Parts
    reuse an existing artist when there is one (see find_split_candidates),
    otherwise get created with the joined artist's origin. The joined artist
    row is deleted once nothing references it. Returns the artists the songs
    now point to. Caller commits.

    Pass the same `created` dict (normalized name -> Artist) to every call
    of one batch: two joined names sharing a new part ("Bass Astral x A",
    "Bass Astral x B") must end up with one "Bass Astral", not two."""
    if created is None:
        created = {}
    joined, proposal = candidate.artist, candidate.proposal

    def resolve(name: str) -> Artist:
        if name in candidate.existing:
            return candidate.existing[name]
        key = normalize(name)
        if key not in created:
            created[key] = Artist(name=name, origin=joined.origin)
            session.add(created[key])
        return created[key]

    primary, *co_mains = [resolve(n) for n in proposal.main]
    feats = [resolve(n) for n in proposal.feat]
    session.flush()

    moved = list(joined.songs)
    for song in moved:
        song.artist = primary
        for a in co_mains:
            _add_quietly(song, a, ADDITIONAL_ARTIST_ROLE_MAIN)
        for a in feats:
            _add_quietly(song, a, ADDITIONAL_ARTIST_ROLE_FEAT)

    # The joined artist credited as an extra on someone else's song: replace
    # it with its parts, guests staying guests
    for link in list(joined.additional_song_links):
        song, role = link.song, link.role
        song.additional_artist_links.remove(link)
        for a in [primary, *co_mains]:
            _add_quietly(song, a, role)
        for a in feats:
            _add_quietly(song, a, ADDITIONAL_ARTIST_ROLE_FEAT)

    session.flush()
    if not joined.songs and not joined.additional_song_links:
        # Album rows it still owns go to the first part, like every artist
        # removal (artist_reassignment.remove_unused_artist()): deleted with
        # them, their artist_id would be NULL - a compilation
        reassign_artist_albums(session, joined.id, primary.id)
        session.delete(joined)
    session.flush()
    # Songs sharing an album move one by one; the first ones lost their album
    # row while it still had the joined artist's songs too
    link_songs(session, moved)
    return [primary, *co_mains, *feats]
