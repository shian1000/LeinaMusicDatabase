"""Clean names (Manage database -> "Clean names") on the stored catalog:
sanitize's "stored" steps applied to every song's title, artist name and
album text, plus the repairs that need a whole record or the database
(Polish mojibake across a song's fields, decomposed letters in every
column, look-alike letters as a proposal). The menu prints and asks; this
module changes rows in the session it's given and never commits.

An artist row is shared by all its songs, so it's never renamed onto a name
another artist already has: the rename is held in `held` (artist id ->
(artist, new name)) for artist_change.review_held_artist_renames(), and a
later step works on the held name.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import object_session

from utils.common.normalizer import name_key
from utils.common.sanitize.text import fix_cp1250_mojibake, fix_cp1250_mojibake_by_words, has_cp1250_mojibake
from utils.common.sanitize import FIELD_ORDER, MODE_AUTO, SOURCE_STORED, STAGE_FIELD, STEPS, Context, is_enabled
from utils.database.albums import rename_album
from utils.database.artist_reassignment import artists_named
from utils.database.database_management import decomposed_names
from utils.database.datatables import Album, Artist, Song

# (field, read, write) of the three names a song carries
CLEANUP_FIELDS = [
    ("title",  lambda song: song.title,        lambda song, v: setattr(song, "title", v)),
    ("artist", lambda song: song.artist.name,  lambda song, v: setattr(song.artist, "name", v)),
    ("album",  lambda song: song.album,        lambda song, v: setattr(song, "album", v)),
]
_SETTERS = {name: write for name, _read, write in CLEANUP_FIELDS}


@dataclass
class Change:
    """What one pass changed on one song (`song`), or on an album row
    (`song` None): "title: 'X'" ..., for the menu to print."""
    song: Optional[Song]
    fields: list = field(default_factory=list)


def hold_artist_rename(held: dict | None, artist: Artist, new_name: str) -> bool:
    """Hold the rename of `artist` to `new_name` in `held` when another
    artist already has that name (or an earlier rename of it is held).
    True = held, so the caller doesn't rename."""
    if held is None:
        return False
    if artist.id not in held:
        session = object_session(artist)
        if session is None or not artists_named(session, new_name, exclude=artist):
            return False
    held[artist.id] = (artist, new_name)
    return True


def apply_step(songs: list, step_name: str, held: dict | None = None, fields=("title", "artist", "album")) -> list[Change]:
    """sanitize's step `step_name` applied to `fields` of every song in
    `songs` - an empty value is skipped, as is a step that would empty one;
    an artist rename onto another artist's name is held
    (hold_artist_rename()). One Change per song changed."""
    step = STEPS[step_name]
    changes = []
    for song in songs:
        changed = []
        for field_name, read, write in CLEANUP_FIELDS:
            if field_name not in fields or field_name not in step.fields:
                continue
            value = read(song)
            is_artist = field_name == "artist"
            if is_artist and held and song.artist.id in held:
                value = held[song.artist.id][1]
            if not value:
                continue
            new_value = step.func(value, Context(field=field_name, source=SOURCE_STORED, stage=STAGE_FIELD,
                                                 artist=song.artist.name))
            if new_value == value or not new_value:
                # A stored name is never cleaned down to nothing
                continue
            if is_artist and hold_artist_rename(held, song.artist, new_value):
                changed.append(f"artist: '{new_value}' (held: another artist has that name)")
                continue
            write(song, new_value)
            changed.append(f"{field_name}: '{new_value}'")
        if changed:
            changes.append(Change(song, changed))
    return changes


# The "stored" steps Clean names runs on its own terms rather than as a pass
# over every song: "nfc" is compose_decomposed_letters() (every column), the
# junk phrases only the unchecked songs' titles (seek_nonsense_names())
SEPARATE_STORED_STEPS = ("nfc", "junk_phrases_any_bracket")


def stored_step_passes() -> tuple[list[str], list[str]]:
    """The "stored" steps Clean names runs over every song, in order: (the
    ones before the Polish repair and composing letters, the ones after) -
    where "nfc" sits in sanitize.FIELD_ORDER. Review steps aren't passes."""
    before, after = [], []
    passes = before
    for name in FIELD_ORDER:
        if name == "nfc":
            passes = after
        step = STEPS[name]
        if step.mode == MODE_AUTO and name not in SEPARATE_STORED_STEPS and is_enabled(name, SOURCE_STORED):
            passes.append(name)
    return before, after


def compose_decomposed_letters(session, held: dict | None = None) -> list[str]:
    """Names stored with decomposed letters ("a" + a combining accent
    instead of "á", from YouTube titles and macOS file names) composed, in
    every column the models compose on write - albums' and artists' too, not
    just the songs' title/artist/album (the "nfc" step, done for the whole
    database). One description per name."""
    changes = []
    for row, attribute, _old, new in decomposed_names(session):
        if isinstance(row, Artist) and attribute == "name" and hold_artist_rename(held, row, new):
            changes.append(f"artist '{new}' (held: another artist has that name)")
            continue
        setattr(row, attribute, new)
        if isinstance(row, Song):
            changes.append(f"{attribute} of {row.artist.name} - {row.title}")
        elif isinstance(row, Album):
            changes.append(f"album {attribute}: {row.title}")
        else:
            changes.append(f"artist {attribute}: {row.name}" if attribute == "name" else f"{attribute} of artist {row.name}")
    return changes


def known_words(songs) -> set[str]:
    """Every word of the titles, albums and artist names, casefolded, edge
    punctuation dropped - what a re-read word must be to count as repaired."""
    words = set()
    for song in songs:
        for _name, read, _write in CLEANUP_FIELDS:
            for word in (read(song) or "").split():
                words.add(re.sub(r"^\W+|\W+$", "", word.casefold()))
    return words


def repair_polish_mojibake(session, songs: list, held: dict | None = None) -> list[Change]:
    """Polish names imported before the mp3 import repaired them ("S³oneczna
    strona miasta" -> "Słoneczna ..."), nothing asked: fix_cp1250_mojibake()
    only acts on characters real text never has - or on every field of a
    song one field of which has one (title "¯ycie jest jak tramwaj", album
    "Mêskie Granie": the whole tag was mis-read). Album rows first, through
    rename_album(), so a repaired title meeting its correct twin merges with
    it instead of leaving an empty album row behind."""
    changes = []
    misread = {song.id for song in songs
               if any(has_cp1250_mojibake(read(song)) for _name, read, _write in CLEANUP_FIELDS)}
    known = known_words(songs)
    albums = session.query(Album).all()
    # From the loaded songs, not album.songs: that's a query per album (~3 s).
    # Read after the query above, whose autoflush relinks the album ids
    misread_albums = {song.album_id for song in songs if song.id in misread}
    for album in albums:
        if album.id in misread_albums:
            fixed = fix_cp1250_mojibake(album.title, force=True)
        else:
            fixed = fix_cp1250_mojibake_by_words(fix_cp1250_mojibake(album.title), known)
        if fixed != album.title:
            changes.append(Change(None, [f"album: '{fixed}'"]))
            rename_album(session, album, fixed)
    # The rest of a mis-read song's own fields; an artist row is shared by
    # other songs, so only its own sure sign (below) repairs its name
    for song in songs:
        if song.id not in misread:
            continue
        changed = []
        for attribute in ("title", "album"):
            value = getattr(song, attribute)
            fixed = fix_cp1250_mojibake(value, force=True)
            if fixed != value:
                setattr(song, attribute, fixed)
                changed.append(f"{attribute}: '{fixed}'")
        if changed:
            changes.append(Change(song, changed))
    for song in songs:
        changed = []
        for field_name, read, write in CLEANUP_FIELDS:
            value = read(song)
            if field_name == "artist" and held and song.artist.id in held:
                value = held[song.artist.id][1]
            fixed = fix_cp1250_mojibake_by_words(fix_cp1250_mojibake(value), known) if value else value
            if fixed == value:
                continue
            if field_name == "artist" and hold_artist_rename(held, song.artist, fixed):
                changed.append(f"artist: '{fixed}' (held: another artist has that name)")
                continue
            write(song, fixed)
            changed.append(f"{field_name}: '{fixed}'")
        if changed:
            changes.append(Change(song, changed))
    return changes


@dataclass
class Proposal:
    """One fix offered for review: `old` -> `new` in `field` of `songs`."""
    field: str
    old: str
    new: str
    songs: list


def review_step_proposals(songs: list, step_name: str) -> list[Proposal]:
    """What the review step `step_name` (sanitize, e.g.
    "lookalike_letters": "Вjörk" -> "Björk") would change: one proposal per
    song title, per artist and per album text, however many songs share it."""
    step = STEPS[step_name]
    proposals = {}  # (field, key) -> Proposal
    for song in songs:
        for field_name, read, _write in CLEANUP_FIELDS:
            if field_name not in step.fields:
                continue
            value = read(song)
            fixed = step.func(value, Context(field=field_name, source=SOURCE_STORED, stage=STAGE_FIELD)) if value else value
            if fixed == value:
                continue
            key = {"title": song.id, "artist": song.artist.id, "album": value}[field_name]
            proposals.setdefault((field_name, key), Proposal(field_name, value, fixed, [])).songs.append(song)
    return list(proposals.values())


def apply_proposals(proposals: list[Proposal], held: dict | None = None) -> int:
    """Apply the ticked `proposals`; an artist fix onto another artist's
    name is held. How many were applied."""
    applied = 0
    for proposal in proposals:
        if proposal.field == "artist" and hold_artist_rename(held, proposal.songs[0].artist, proposal.new):
            continue
        for song in proposal.songs:
            _SETTERS[proposal.field](song, proposal.new)
        applied += 1
    return applied


def junk_title_fix(title: str | None) -> str | None:
    """The title without trailing junk ("Song (Official Video)" -> "Song",
    the "junk_phrases_any_bracket" step), or None when nothing changes."""
    if not title:
        return None
    fixed = STEPS["junk_phrases_any_bracket"].func(title, Context(field="title", source=SOURCE_STORED, stage=STAGE_FIELD))
    return fixed if fixed != title else None


@dataclass
class FeatMove:
    """A stored title whose featured-artist credits move to the song's
    credits: "Firestone ft. Conrad Sewell" -> "Firestone", feat Conrad
    Sewell. `known`: each name the database already has as an artist (the
    credit reuses that row); `suspicious`: a name that looks like more than
    one ("Andriy Khlyvnyuk of Boombox", "David Bowie Holding out for a
    Hero") - offered unticked."""
    song: Song
    title: str
    names: list
    known: dict = field(default_factory=dict)
    suspicious: bool = False


_SUSPICIOUS_NAME_RE = re.compile(r"[()\[\]/]|\.\.\.|\bof\b", re.IGNORECASE)
FEAT_NAME_MAX_WORDS = 4


def feat_move_proposals(session, songs: list) -> list[FeatMove]:
    """Every song of `songs` whose title credits featured artists
    (artist_credits.split_feat_credits(), the import's own rule)."""
    from utils.common.artist_credits import split_feat_credits

    known_keys = None
    moves = []
    for song in songs:
        title, names = split_feat_credits(song.title or "")
        if not names:
            continue
        if known_keys is None:
            known_keys = {name_key(n) for row in session.query(Artist.name, Artist.name_latin).all() for n in row if n}
        moves.append(FeatMove(
            song=song, title=title, names=names,
            known={n: name_key(n) in known_keys for n in names},
            suspicious=any(len(n.split()) > FEAT_NAME_MAX_WORDS or _SUSPICIOUS_NAME_RE.search(n) for n in names),
        ))
    return moves


def apply_feat_moves(session, moves: list[FeatMove]) -> list[Artist]:
    """Credit each move's names as featured artists of its song (an artist
    the database has is reused, else a new one) and give the song the title
    without them. Returns the artists created, for the split review."""
    from utils.database.song_duplicates import credit_feat_artists

    before = {a.id for a in session.query(Artist.id).all()}
    for move in moves:
        credit_feat_artists(session, move.song, move.names)
        move.song.title = move.title
    session.flush()
    return [a for a in session.query(Artist).all() if a.id not in before]
