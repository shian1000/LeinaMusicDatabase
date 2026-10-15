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

YouTube links set by hand (app handoff musicdatabase-youtube-links-by-hand.md):
songs.youtube_video_id (a video id, "N/A" or empty) and
songs.music_video_youtube_id (a video id or empty, migration 0012). The phone
plays / opens its user's link at once and keeps doing so until music.db's
value stops being the old_value - a rejected one keeps that phone on the
user's link. Accepting a youtube_video_id is a manual pick: the video comes
off bad_youtube_videos, and the owner may have the replaced video remembered
as wrong for the song. check_correction_videos() asks YouTube whether the
new videos play, for the automatic pass and the review. A newer
youtube_video_id correction settles older dead link / found link reports of
that song (inbox_requests.links_set_by_hand()).

Taking a correction back: the phone sends a newer report with new_value ==
old_value. It's ALREADY_SET, and that is recorded in decisions.json as of the
newest report, so an older report of the field waiting in another file (in
review/ while the takeback is in new/) is then ANSWERED_EARLIER, never
offered on its own.
"""

from dataclasses import dataclass, field
from datetime import datetime

from config.constants import ALBUM_TYPE_SINGLE, ALBUM_TYPES, BAD_VIDEO_WRONG_FOR_SONG
from utils.common.debug import mlog
from utils.common.normalizer import normalize
from utils.database.albums import album_key, album_twin, rename_album
from utils.database.datatables import Album, Artist, Song
from utils.database.inbox_requests import (
    DECIDED,
    FieldCorrection,
    RequestFile,
    load_bad_videos,
    load_decisions,
    mark_bad_videos,
    now_iso,
    parse_time,
    resolve_song_id,
    save_decision,
    unmark_bad_video,
)
from utils.database.latin_names import LATIN_COLUMNS, latin_of
from utils.database.song_artists import song_all_artists
from utils.database.song_references import load_changes
from utils.youtube.manage_youtube_playlists import NO_VIDEO_SENTINEL, extract_video_id

TABLES = {"songs": Song, "artists": Artist, "albums": Album}

TEXT = "text"
INTEGER = "integer"
ALBUM_TYPE = "album type"
YOUTUBE_LINK = "youtube link"  # a video id or NO_VIDEO_SENTINEL
MUSIC_VIDEO = "music video"  # a video id
VIDEO_KINDS = (YOUTUBE_LINK, MUSIC_VIDEO)

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
    ("songs", "youtube_video_id"): (YOUTUBE_LINK, True),
    ("songs", "music_video_youtube_id"): (MUSIC_VIDEO, True),
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
    ("songs", "youtube_video_id"): "YouTube video",
    ("songs", "music_video_youtube_id"): "music video",
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
    if kind in VIDEO_KINDS:
        if kind == YOUTUBE_LINK and value == NO_VIDEO_SENTINEL:
            return value, None
        # The phone sends bare ids; a link is accepted for the owner's own edit
        video_id = extract_video_id(value)
        if video_id is None:
            return None, f"{value!r} isn't a YouTube video id or link"
        return video_id, None
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
    # YouTube columns: why the new video was found bad before (for this song)
    bad: str | None = None
    # Filled by check_correction_videos(): {video id: True plays / False gone / None unknown}
    plays: dict = field(default_factory=dict)
    checked_at: str | None = None

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
    def is_video(self) -> bool:
        """One of the YouTube link columns."""
        return FIELDS.get((self.table_name, self.column_name), (None,))[0] in VIDEO_KINDS

    @property
    def video_id(self) -> str | None:
        """The video the correction sets (not "N/A" or empty), else None."""
        if not self.is_video or self.error:
            return None
        value = self.new_value
        return value if value and value != NO_VIDEO_SENTINEL else None

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

    bad_videos = None
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
            _record_already_set(decisions, case)
        elif _answered_after(decisions.get(case.key), case.reports):
            case.status = ANSWERED_EARLIER
        elif case.column_name == "youtube_video_id" and case.video_id:
            if bad_videos is None:
                bad_videos = load_bad_videos(session)
            case.bad = bad_videos.why(case.video_id, case.row_id)
    return list(cases.values())


def _record_already_set(decisions: dict, case: FieldCorrectionCase) -> None:
    """Remember that the database held the newest report's value, as of that
    report: an older report of the field in a file read another time (a
    taken-back correction waiting in review/) is then answered earlier."""
    made = parse_time(case.latest.corrected_at)
    if made is None:
        return
    when = made.isoformat(timespec="seconds")
    previous = decisions.get(case.key) or {}
    previous_time = parse_time(previous.get("decided_at"))
    if previous_time is not None and previous_time >= made:
        return
    entry = {"decision": ALREADY_SET, "value": case.new_value, "decided_at": when,
             "files": sorted({r.file_name for r in case.reports})}
    decisions[case.key] = entry
    save_decision(case.key, entry)


# ==================== YouTube links ====================

def check_correction_videos(cases: list[FieldCorrectionCase]) -> dict:
    """Whether the videos open YouTube link corrections set play: one Data
    API call for all (its "gone" is final), yt-dlp for the rest, once per
    video - as check_link_cases() does. Fills plays / checked_at. Returns
    {"api": bool, "blocked": n} for the screen."""
    from utils.youtube.manage_youtube_playlists import video_playability, videos_exist_via_api

    cases = [c for c in cases if c.video_id]
    if not cases:
        return {"api": False, "blocked": 0}
    api = videos_exist_via_api([c.video_id for c in cases])
    known, blocked = {}, 0
    for case in cases:
        video_id = case.video_id
        if video_id not in known:
            if api is not None and api.get(video_id) is False:
                known[video_id] = False
            else:
                answer = video_playability(video_id)
                if answer is None:
                    blocked += 1
                    answer = True if api is not None and api.get(video_id) else None
                known[video_id] = answer
        case.plays = {video_id: known[video_id]}
        case.checked_at = now_iso()
    return {"api": api is not None, "blocked": blocked}


def replaced_video(case: FieldCorrectionCase, value) -> str | None:
    """For a youtube_video_id answer: the video it replaces (one to maybe
    remember as wrong for the song), or None (none, "N/A", the same one)."""
    if case.column_name != "youtube_video_id" or case.row is None:
        return None
    current = case.current
    if not current or current == NO_VIDEO_SENTINEL or same_value(current, value):
        return None
    return current


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
                           by: str | None = None, old_video_wrong: bool = False) -> None:
    """Store the answer: ACCEPT the phone's value, EDIT = `value` instead
    (already coerce_field_value()d), RENAME_ALBUM = a songs.album value
    (`value`, or the phone's) given to the song's whole album, REJECT = keep
    the database's, MERGE_ARTIST = an artists.name correction that names
    another artist (`value` = that Artist, from artist_twins()): this one is
    merged into it, the usual artist merge. `by` = "auto" for the automatic
    pass. A youtube_video_id set is a manual pick: the video comes off
    bad_youtube_videos, and with `old_video_wrong` the replaced one goes on
    it as wrong for the song. The caller commits the music
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
        if case.column_name == "youtube_video_id":
            if value and value != NO_VIDEO_SENTINEL:
                unmark_bad_video(session, value, case.row.id)
            if old_video_wrong and old and old != NO_VIDEO_SENTINEL and old != value:
                mark_bad_videos(session, [old], BAD_VIDEO_WRONG_FOR_SONG, case.row.id)
        session.flush()

    save_decision(case.key, {
        "decision": decision,
        "value": value if decision != REJECT else case.new_value,
        "decided_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": sorted({r.file_name for r in case.reports}),
        **({"by": by} if by else {}),
        **({"old_video_wrong": old} if old_video_wrong and decision != REJECT else {}),
    })
    case.status = DECIDED
    mlog(f"Inbox request: {case.table_name} {case.row_id} {case.column_name}: {old!r} -> {decision} {value!r}")
