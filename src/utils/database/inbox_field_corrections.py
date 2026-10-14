"""
Field corrections from the phone (the `field_corrections` table of a request
file, app handoff docs/handoffs/musicdatabase-field-corrections.md): the user
corrected a value of music.db in the phone's song info. Never applied without
review - Manage database -> "Resolve new requests"
(utils/ui/inbox_request_review.py) asks; nothing here asks anything (ADR-0001).
Reading the files, decisions.json and moving finished files are shared with
dead link reports (inbox_requests.py).

Cases: reports are grouped by (table, current row, column) - repeats of one
field from several files are asked about once, and the newest `corrected_at`
is the value offered. songs ids follow merges through song_id_changes;
artists and albums have no such log, so a row that's gone (merged or
deleted) settles the case. Settled without asking:
- the row is gone;
- the database already holds the new value;
- the owner answered that field after the newest report was made.
Still asked, but flagged: the value changed since the phone's music.db
(`changed_since`), or the song no longer has that artist / album.

Applying is an ordinary edit through the ORM, so the album listener does
what it does after any edit: a corrected songs.album moves the song to the
album row of the new name (or the owner renames the whole album instead,
rename_album()), and a corrected albums.title retitles its songs' album text.
Latin forms (title_latin / name_latin, utils/database/latin_names.py) are
plain text fields; a one-song "Singles" album copies its song's form through
the same listener. A renamed title or name keeps its Latin form -
latin_form_to_check() lets the review point at it.
"""

from dataclasses import dataclass, field
from datetime import datetime

from config.constants import ALBUM_TYPE_SINGLE, ALBUM_TYPES
from utils.common.debug import mlog
from utils.common.normalizer import normalize
from utils.database.albums import album_key, album_twin, rename_album
from utils.database.datatables import Album, Artist, Song
from utils.database.inbox_requests import (
    DECIDED,
    FieldCorrection,
    RequestFile,
    load_decisions,
    parse_time,
    resolve_song_id,
    save_decision,
)
from utils.database.latin_names import LATIN_COLUMNS, latin_of
from utils.database.song_artists import song_all_artists
from utils.database.song_references import load_changes

TABLES = {"songs": Song, "artists": Artist, "albums": Album}

TEXT = "text"
INTEGER = "integer"
ALBUM_TYPE = "album type"

# What the phone may correct: (table, column) -> (kind, may be emptied).
# Nothing else is ever sent; anything else is kept for a later version.
FIELDS = {
    ("songs", "title"): (TEXT, False),
    ("songs", "title_latin"): (TEXT, True),
    ("songs", "album"): (TEXT, True),
    ("songs", "language"): (TEXT, True),
    ("songs", "year"): (INTEGER, True),
    ("songs", "track_number"): (INTEGER, True),
    ("songs", "disc_number"): (INTEGER, True),
    ("artists", "name"): (TEXT, False),
    ("artists", "name_latin"): (TEXT, True),
    ("artists", "origin"): (TEXT, True),
    ("artists", "synonyms"): (TEXT, True),
    ("albums", "title"): (TEXT, False),
    ("albums", "title_latin"): (TEXT, True),
    ("albums", "year"): (INTEGER, True),
    ("albums", "type"): (ALBUM_TYPE, True),
}

FIELD_LABELS = {
    ("songs", "title"): "title",
    ("songs", "title_latin"): "song title in Latin letters",
    ("songs", "album"): "album",
    ("songs", "language"): "language",
    ("songs", "year"): "year",
    ("songs", "track_number"): "track number",
    ("songs", "disc_number"): "disc number",
    ("artists", "name"): "artist's name",
    ("artists", "name_latin"): "artist's name in Latin letters",
    ("artists", "origin"): "artist's origin",
    ("artists", "synonyms"): "artist's synonyms",
    ("albums", "title"): "album's title",
    ("albums", "title_latin"): "album title in Latin letters",
    ("albums", "year"): "album's year",
    ("albums", "type"): "album's type",
}

# Case statuses: OPEN needs an answer, UNSUPPORTED is kept for a later
# version (both keep their files in new/); the rest are settled
OPEN = "open"
UNSUPPORTED = "field this version can't correct"
ROW_GONE = "no longer in the database"
ALREADY_SET = "database already has that value"
ANSWERED_EARLIER = "answered earlier"

# Answers
ACCEPT = "accepted"
EDIT = "edited"
RENAME_ALBUM = "renamed album"  # a songs.album correction applied to its whole album
MERGE_ARTIST = "merged artist"  # an artists.name correction naming another artist
REJECT = "rejected"


def coerce_field_value(table: str, column: str, value):
    """(value as it would be stored, error or None). Text is trimmed, "" is
    empty; integers may come as digit strings."""
    kind, nullable = FIELDS[(table, column)]
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            value = None
    if value is None:
        return (None, None) if nullable else (None, "can't be empty")
    if kind == INTEGER:
        if isinstance(value, bool):
            return None, f"{value!r} isn't a number"
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, str) and value.lstrip("-").isdigit():
            value = int(value)
        if not isinstance(value, int):
            return None, f"{value!r} isn't a whole number"
        return value, None
    value = str(value)
    if kind == ALBUM_TYPE and value not in ALBUM_TYPES:
        return None, f"{value!r} isn't one of {', '.join(ALBUM_TYPES)}"
    return value, None


def same_value(a, b) -> bool:
    """Equal as stored values: empty == empty, 1990 == "1990", text exact
    (a case-only fix is a real correction)."""
    a = a.strip() if isinstance(a, str) else a
    b = b.strip() if isinstance(b, str) else b
    a = None if a == "" else a
    b = None if b == "" else b
    if a is None or b is None:
        return a is b
    return str(a) == str(b)


def _decision_key(table: str, row_id, column: str) -> str:
    return f"field:{table}:{row_id}:{column}"


@dataclass
class FieldCorrectionCase:
    """One field of one row, from however many reports (oldest first)."""
    table_name: str
    row_id: int | None  # current id; None = the row is gone
    column_name: str
    reports: list[FieldCorrection]
    row: object = None  # the Song / Artist / Album
    song: Song | None = None  # where the newest report was made from, after merges
    status: str = OPEN
    auto_note: str | None = None  # why the automatic pass left it to the owner

    @property
    def latest(self) -> FieldCorrection:
        return self.reports[-1]

    @property
    def label(self) -> str:
        return FIELD_LABELS.get((self.table_name, self.column_name),
                                f"{self.table_name}.{self.column_name}")

    @property
    def key(self) -> str:
        return _decision_key(self.table_name, self.row_id, self.column_name)

    @property
    def pending(self) -> bool:
        return self.status in (OPEN, UNSUPPORTED)

    @property
    def current(self):
        return getattr(self.row, self.column_name) if self.row is not None else None

    @property
    def new_value(self):
        return coerce_field_value(self.table_name, self.column_name, self.latest.new_value)[0]

    @property
    def error(self) -> str | None:
        """The phone's value can't be stored as it is (only editing is offered)."""
        return coerce_field_value(self.table_name, self.column_name, self.latest.new_value)[1]

    @property
    def changed_since(self) -> bool:
        """The database's value isn't the one the phone had any more (fixed
        here already, or another phone's correction)."""
        return not same_value(self.current, self.latest.old_value)

    @property
    def earlier_values(self) -> list:
        """Other values older reports asked for (the newest wins)."""
        seen = []
        for report in self.reports[:-1]:
            if not same_value(report.new_value, self.latest.new_value) and \
                    not any(same_value(report.new_value, v) for v in seen):
                seen.append(report.new_value)
        return seen

    @property
    def title_differs(self) -> bool:
        """The phone showed another title for the song (renamed, or merged)."""
        if self.song is None:
            return False
        return any(normalize(r.title or "") != normalize(self.song.title or "") for r in self.reports)

    @property
    def not_on_song(self) -> bool:
        """The artist / album is no longer that song's - row_id may not be
        what the user meant."""
        if self.song is None or self.row is None:
            return False
        if self.table_name == "artists":
            return self.row not in song_all_artists(self.song)
        if self.table_name == "albums":
            return self.song.album_id != self.row.id
        return False


def _answered_after(decision: dict | None, reports: list[FieldCorrection]) -> bool:
    if not decision:
        return False
    decided = parse_time(decision.get("decided_at"))
    made = [t for t in (parse_time(r.corrected_at) for r in reports) if t]
    return decided is not None and len(made) == len(reports) and all(t <= decided for t in made)


def _report_order(report: FieldCorrection):
    made = parse_time(report.corrected_at)
    return (made is not None, made.timestamp() if made else 0.0, report.file_name)


def collect_field_correction_cases(session, requests: list[RequestFile]) -> list[FieldCorrectionCase]:
    """Group every supported file's field corrections by (table, current
    row, column) and settle what needs no answer. Order: as first reported."""
    merged_into = {old: new for _, old, new in load_changes()}
    decisions = load_decisions()
    cases: dict[tuple, FieldCorrectionCase] = {}
    for request in requests:
        if not request.supported:
            continue
        for report in request.field_corrections:
            row_id = report.row_id
            if report.table_name == "songs" and row_id is not None:
                row_id = resolve_song_id(row_id, merged_into)
            key = ((report.table_name, row_id, report.column_name) if row_id is not None
                   else (report.table_name, "gone", report.row_id, report.column_name))
            case = cases.get(key)
            if case is None:
                case = cases[key] = FieldCorrectionCase(report.table_name, row_id, report.column_name, [])
            case.reports.append(report)

    for case in cases.values():
        case.reports.sort(key=_report_order)
        model = TABLES.get(case.table_name)
        if (case.table_name, case.column_name) not in FIELDS:
            case.status = UNSUPPORTED
            continue
        case.row = session.get(model, case.row_id) if case.row_id is not None else None
        song_id = resolve_song_id(case.latest.song_id, merged_into) if case.latest.song_id is not None else None
        case.song = session.get(Song, song_id) if song_id is not None else None
        if case.row is None:
            case.status = ROW_GONE
        elif case.error is None and same_value(case.current, case.new_value):
            case.status = ALREADY_SET
        elif _answered_after(decisions.get(case.key), case.reports):
            case.status = ANSWERED_EARLIER
    return list(cases.values())


# ==================== what an answer would touch ====================

def artist_twins(session, case: FieldCorrectionCase, name) -> list[Artist]:
    """Other artists already called `name` (normalized) - renaming makes
    the same artist spelt twice, for the owner to merge."""
    if case.table_name != "artists" or case.column_name != "name" or not name:
        return []
    wanted = normalize(name)
    return [a for a in session.query(Artist).filter(Artist.id != case.row.id).all()
            if normalize(a.name or "") == wanted]


def album_title_twin(session, case: FieldCorrectionCase, title) -> Album | None:
    """The album an albums.title correction would merge this one into."""
    if case.table_name != "albums" or case.column_name != "title" or not title:
        return None
    return album_twin(session, case.row, title)


def latin_form_to_check(case: FieldCorrectionCase, value) -> str | None:
    """For a title / name correction: the row's Latin form, which stays as
    it is - the owner may want to change it too. None when there's none."""
    if case.row is None or case.column_name not in ("title", "name") or value is None:
        return None
    if same_value(case.current, value):
        return None
    return latin_of(case.row) if type(case.row) in LATIN_COLUMNS else None


def song_album_to_rename(case: FieldCorrectionCase) -> Album | None:
    """For a songs.album correction: the song's album row when renaming
    that whole album (not just moving this song off it) is an option - a
    real album with other songs on it."""
    if case.table_name != "songs" or case.column_name != "album" or case.row is None:
        return None
    album = case.row.album_entry
    if album is None or album.type == ALBUM_TYPE_SINGLE or len(album.songs) < 2:
        return None
    if album_key(album.title) != album_key(case.row.album):
        return None
    return album


# ==================== answers ====================

def apply_field_correction(session, case: FieldCorrectionCase, decision: str, value=None,
                           by: str | None = None) -> None:
    """Store the answer: ACCEPT the phone's value, EDIT = `value` instead
    (already coerce_field_value()d), RENAME_ALBUM = a songs.album value
    (`value`, or the phone's) given to the song's whole album, REJECT = keep
    the database's, MERGE_ARTIST = an artists.name correction that names
    another artist (`value` = that Artist, from artist_twins()): this one is
    merged into it, the usual artist merge. `by` = "auto" for the automatic
    pass. The caller commits the music
    session (for MERGE_ARTIST it must be the global one, which the merge
    uses)."""
    if decision == ACCEPT:
        if case.error:
            raise ValueError(f"Leina Radio's value can't be stored: {case.error}")
        value = case.new_value
    elif decision in (EDIT, RENAME_ALBUM):
        if decision == RENAME_ALBUM and value is None:
            value = case.new_value
        value, error = coerce_field_value(case.table_name, case.column_name, value)
        if error:
            raise ValueError(error)
    elif decision == MERGE_ARTIST:
        if not isinstance(value, Artist) or case.table_name != "artists":
            raise ValueError("MERGE_ARTIST needs the Artist to merge into")
    elif decision != REJECT:
        raise ValueError(f"Unknown decision {decision!r}")

    old = case.current
    if decision == MERGE_ARTIST:
        from utils.database.database_management import merge_artists_in_db
        merge_artists_in_db(case.row, value)
        value = value.id
    elif decision == RENAME_ALBUM:
        album = song_album_to_rename(case)
        if album is None or value is None:
            raise ValueError("Nothing to rename: the song isn't on an album with other songs")
        rename_album(session, album, value)
    elif decision != REJECT:
        if case.table_name == "albums" and case.column_name == "title":
            case.row = rename_album(session, case.row, value)
        else:
            setattr(case.row, case.column_name, value)
        session.flush()

    save_decision(case.key, {
        "decision": decision,
        "value": value if decision != REJECT else case.new_value,
        "decided_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": sorted({r.file_name for r in case.reports}),
        **({"by": by} if by else {}),
    })
    case.status = DECIDED
    mlog(f"Inbox request: {case.table_name} {case.row_id} {case.column_name}: {old!r} -> {decision} {value!r}")
