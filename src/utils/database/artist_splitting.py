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
from dataclasses import dataclass, field
from pathlib import Path

from config.constants import (
    ADDITIONAL_ARTIST_ROLE_FEAT,
    ADDITIONAL_ARTIST_ROLE_MAIN,
    ARTIST_SPLIT_IGNORE_FILE,
)
from utils.common.normalizer import normalize
from utils.common.text_utils import is_soundtrack_artist
from utils.database.datatables import Artist, Song
from utils.database.song_artists import add_additional_artist

_IGNORE_PATH = Path(__file__).resolve().parents[3] / ARTIST_SPLIT_IGNORE_FILE

# "A feat. B", "A ft. B", "A featuring B", "A (feat. B)" - everything after is a guest
_FEAT = re.compile(r"\s*\(?\s*\b(?:feat\.?|ft\.?|featuring)\s+", re.IGNORECASE)
# Between equal artists. " x " is lowercase-only on purpose: an uppercase X is
# usually part of a name ("Final Fantasy X", "Malcolm X"). "and"/"i" are left
# out - too many band names ("Hall and Oates" style) use them.
_MAIN_SEPARATOR = re.compile(r",\s+|\s+&\s+|\s+x\s+|\s+/\s+|\s+\+\s+|\s+vs\.?\s+")
# Strong enough to pre-select a proposal on its own; "&" and "," aren't
# ("Simon & Garfunkel", "Earth, Wind & Fire").
_CONFIDENT_SEPARATOR = re.compile(r"\s+x\s+")


@dataclass
class SplitProposal:
    original: str
    main: list[str]
    feat: list[str] = field(default_factory=list)
    confident: bool = False

    @property
    def names(self) -> list[str]:
        return [*self.main, *self.feat]


def _split_slashes(part: str) -> list[str]:
    """Spaceless "/" splits only when it can't be one name: "Ben Folds/Nick
    Hornby" or "KLEKS/IGO/Mrozu" yes, "AC/DC" no."""
    pieces = [p.strip() for p in part.split("/")]
    if len(pieces) > 2 or any(" " in p for p in pieces):
        return pieces
    return [part]


def _split_names(text: str) -> list[str]:
    names = []
    for part in _MAIN_SEPARATOR.split(text):
        names.extend(_split_slashes(part.strip()))
    return [n.strip(" )") for n in names if n.strip(" )")]


def propose_artist_split(name: str) -> SplitProposal | None:
    """Parse a joined artist name, or None when it doesn't look like several
    artists. Purely textual - the caller decides with the user."""
    if not name or is_soundtrack_artist(name):
        return None

    feat_match = _FEAT.search(name)
    main_text, feat_text = (name[:feat_match.start()], name[feat_match.end():]) if feat_match else (name, "")
    main, feat = _split_names(main_text), _split_names(feat_text)

    seen, unique_main, unique_feat = set(), [], []
    for names, out in ((main, unique_main), (feat, unique_feat)):
        for n in names:
            key = normalize(n)
            if key and key not in seen:
                seen.add(key)
                out.append(n)
    if not unique_main or len(unique_main) + len(unique_feat) < 2:
        return None

    confident = bool(feat_match or _CONFIDENT_SEPARATOR.search(main_text))
    # "Big Brother & the Holding Company" - a "the ..." part is a band name;
    # "Pezet - Małolat feat. Małpa" - a dash means the split is incomplete
    if any(n.lower().startswith("the ") for n in unique_main[1:]) or any(" - " in n for n in unique_main + unique_feat):
        confident = False
    return SplitProposal(name, unique_main, unique_feat, confident)


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
        for syn in (artist.synonyms or "").split(","):
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
        proposal = propose_artist_split(artist.name)
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

    for song in list(joined.songs):
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
        session.delete(joined)
    session.flush()
    return [primary, *co_mains, *feats]
