"""
Requests from the MusicDatabaseApp phone client: reading the inbox the
sharing server fills, deciding what is still worth asking about, and
applying the user's answers. The review screen is
utils/ui/inbox_request_review.py (Manage database -> "Resolve new requests");
nothing here asks anything, so a later intake can reuse it (ADR-0001).

Inbox (Settings.phone_inbox_dir, on the data drive):
- new/        - what the sharing server stored (PUT /inbox/<name>.db), not
                looked at yet. The automatic pass (inbox_auto.py) takes them
                from here.
- review/     - files the automatic pass couldn't settle on its own, waiting
                for the owner. new/ + review/ count towards the server's size
                limit, so only settling a file frees room for more.
- processed/  - files whose every row has been settled; kept, not deleted.
                The server answers a resend of one of these with 200.
- rejected/   - files that aren't readable request files.
- decisions.json - every answer given, keyed "<song id>:<dead video id>"
                (found links: "found:<song id>:<video id>").
                Only "kept" answers are looked up again: they leave the
                database unchanged, so without the record the same report
                (from a file still waiting on a skipped row, or another
                phone) would be asked about again.
- auto_checks.json - per case: the automatic pass's YouTube check and why it
                left the case to the owner, shown in the review.
- auto_report.json - what the automatic pass changed; main.py shows the
                entries not shown yet at startup.
- packages.json - the files imported from request packages (a .zip sent by
                hand, request_packages.py): file name -> the sender name
                typed on the phone, the package, when. Their user part is
                nobody's verified account, so RequestFile.sender is None for
                them (no permission ever applies) and the review shows them
                as from that name, from a package.

Videos found bad are kept in music.db's bad_youtube_videos (migration 0010),
which the phone reads too, so a later request naming one is turned down
without asking YouTube again (load_bad_videos()): for every song (removed,
doesn't play here, a dead link that was replaced) or for one song (the owner
turned it down or chose "N/A"). Choosing a video takes it off.

One request file is one SQLite file with a one-row `request` table and one
table per request type (app ADR 0005; format described in the app's
docs/handoffs/musicdatabase-outbox.md). Known types: dead_youtube_links,
field_corrections (handled in inbox_field_corrections.py),
found_youtube_links (inbox_found_links.py) and song_requests
(inbox_song_requests.py). A file with a
table this version doesn't know stays in new/, so a later version can still
handle that part.

A dead link report is settled without asking when:
- its song was deleted since the phone downloaded music.db;
- songs.youtube_video_id already holds another link or "N/A";
- the user kept that link after the report was made;
- a newer youtube_video_id correction of the song is in the same batch (the
  phone's user set the link by hand; that correction is asked about instead).
song_id follows merges through song_id_changes. Reports of the same
(song, dead video) from several files are asked about once. Suggestions
found bad before are left out (LinkCase.bad).
"""

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm import object_session

from config.constants import (
    BAD_VIDEO_NO_VIDEO_FOR_SONG,
    BAD_VIDEO_REASONS,
    BAD_VIDEO_REMOVED,
    BAD_VIDEO_REPLACED_DEAD_LINK,
    BAD_VIDEO_UNPLAYABLE,
    PHONE_REQUEST_FORMAT,
)
from settings import Settings
from utils.common.debug import flog, mlog
from utils.common.normalizer import normalize
from utils.database.datatables import BadYoutubeVideo, Song
from utils.database.latin_names import artist_match_synonyms
from utils.database.song_artists import song_artist_credits, song_main_artist_label
from utils.database.song_references import load_changes
from utils.database.song_kinds import is_full_album
from utils.youtube.manage_youtube_playlists import NO_VIDEO_SENTINEL

INBOX_DIR = Path(str(Settings.phone_inbox_dir)) if Settings.phone_inbox_dir else None
INBOX_DRIVE_MOUNT = Path(str(Settings.sharing_drive_mount)) if Settings.sharing_drive_mount else None
NEW = "new"
REVIEW = "review"
PROCESSED = "processed"
REJECTED = "rejected"
DECISIONS_FILE = "decisions.json"
CHECKS_FILE = "auto_checks.json"
REPORT_FILE = "auto_report.json"
PACKAGES_FILE = "packages.json"
# The user part of a request file name - the server only takes a named file
# from that user's token, so it is a verified sender
_SENDER_RE = re.compile(r"^(?P<user>[a-z0-9][a-z0-9_-]{1,31})_\d{4}-\d{2}-\d{2}_")

DEAD_LINKS_TABLE = "dead_youtube_links"
FIELD_CORRECTIONS_TABLE = "field_corrections"
FOUND_LINKS_TABLE = "found_youtube_links"
SONG_REQUESTS_TABLE = "song_requests"
KNOWN_TABLES = {"request", DEAD_LINKS_TABLE, FIELD_CORRECTIONS_TABLE, FOUND_LINKS_TABLE, SONG_REQUESTS_TABLE}
# Android's SQLiteDatabase adds this to every file it creates (just the locale) -
# not a request, so it never keeps a file in the inbox
PLATFORM_TABLES = {"android_metadata"}

# Case statuses: OPEN needs an answer; the others are settled
OPEN = "open"
SONG_DELETED = "song deleted"
LINK_ALREADY_CHANGED = "link already changed"
ALREADY_KEPT = "link kept earlier"
LINK_SET_BY_HAND = "a newer link was set by hand"
DECIDED = "decided"

# Answers
USE_VIDEO = "replaced"
NO_VIDEO = "no_video"
KEEP = "kept"


@dataclass
class DeadLinkReport:
    file_name: str
    song_id: int
    title: str
    artist: str
    dead_video_id: str
    detected_at: str
    replacement_video_id: str | None = None
    replacement_title: str | None = None
    replacement_channel: str | None = None
    replacement_found_at: str | None = None


@dataclass
class FieldCorrection:
    """A value of music.db the user corrected on the phone (app handoff
    musicdatabase-field-corrections.md)."""
    file_name: str
    table_name: str
    row_id: int
    column_name: str
    old_value: object  # as the phone's music.db had it; None = empty
    new_value: object  # None = the user emptied the field
    song_id: int  # the song whose info it was corrected from
    title: str
    artist: str
    corrected_at: str


@dataclass
class FoundLinkReport:
    """A video the phone found for a song music.db had no link for (app
    handoff musicdatabase-found-youtube-links.md)."""
    file_name: str
    song_id: int
    title: str
    artist: str
    video_id: str
    video_title: str | None
    video_channel: str | None
    found_at: str


@dataclass
class SongRequestReport:
    """A song Leina Radio had that music.db didn't (app handoff
    musicdatabase-song-requests.md): a YouTube video, a Spotify track or a
    file on the phone, by its title and artist as the phone showed them."""
    file_name: str
    title: str
    artist: str | None  # None = the phone knew no artist
    album: str | None
    year: int | None
    duration_s: int | None
    youtube_video_id: str | None
    spotify_track_id: str | None
    source: str  # "youtube", "spotify" or "phone_file"
    requested_at: str


@dataclass
class RequestFile:
    path: Path
    format: int | None = None
    created_at: str | None = None
    user_name: str | None = None
    app: str | None = None
    dead_links: list[DeadLinkReport] = field(default_factory=list)
    field_corrections: list[FieldCorrection] = field(default_factory=list)
    found_links: list[FoundLinkReport] = field(default_factory=list)
    song_requests: list[SongRequestReport] = field(default_factory=list)
    unknown_tables: list[str] = field(default_factory=list)
    error: str | None = None  # not a readable request file
    package: dict | None = None  # packages.json's entry: imported from a request package

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def supported(self) -> bool:
        return self.error is None and self.format == PHONE_REQUEST_FORMAT

    @property
    def sender(self) -> str | None:
        """The logged-in user who sent it (from the file name); None =
        anonymous, or imported from a package (its name is only typed)."""
        if self.package is not None:
            return None
        match = _SENDER_RE.match(self.name)
        return match.group("user") if match else None


@dataclass
class Replacement:
    video_id: str
    title: str | None = None
    channel: str | None = None


@dataclass
class LinkCase:
    """A song's YouTube link the phone wants set: a dead link replaced
    (DeadLinkCase) or a link found for a song without one
    (inbox_found_links.FoundLinkCase). Checked with YouTube the same way."""
    song_id: int | None  # after following merges; None = song deleted
    dead_video_id: str | None  # the link being replaced; None = the song had none
    reports: list
    song: Song | None = None
    status: str = OPEN
    # Filled by check_link_cases() (slow: YouTube)
    # False = the Data API says the dead link is gone; None = not rechecked
    # (the request already says it's dead - one request fewer to YouTube)
    dead_still_plays: bool | None = None
    # Suggested and newfound videos: True plays / False gone / None unknown
    plays: dict = field(default_factory=dict)
    our_video_id: str | None = None
    checked_at: str | None = None  # when the above was filled; None = not checked
    auto_note: str | None = None  # why the automatic pass left it to the owner
    # The phone's videos found bad before (bad_youtube_videos): video id -> why
    bad: dict = field(default_factory=dict)

    @property
    def pending(self) -> bool:
        """Keeps its files in new/."""
        return self.status == OPEN

    def all_replacements(self) -> list[Replacement]:
        """Every video the phone named for the song, known bad ones too."""
        raise NotImplementedError

    @property
    def replacements(self) -> list[Replacement]:
        """The phone's videos worth considering."""
        return [r for r in self.all_replacements() if r.video_id not in self.bad]

    @property
    def bad_replacements(self) -> list[Replacement]:
        return [r for r in self.all_replacements() if r.video_id in self.bad]

    def find_bad(self, bad_videos) -> None:
        self.bad = {r.video_id: why for r in self.all_replacements()
                    if (why := bad_videos.why(r.video_id, self.song_id))}

    @property
    def title_differs(self) -> bool:
        """The phone showed another title for this id (e.g. renamed, or merged into another song)."""
        if self.song is None:
            return False
        return any(normalize(r.title or "") != normalize(self.song.title or "") for r in self.reports)


@dataclass
class DeadLinkCase(LinkCase):
    """One (song, dead video) pair, from however many reports."""

    @property
    def key(self) -> str:
        return _decision_key(self.song_id, self.dead_video_id)

    def all_replacements(self) -> list[Replacement]:
        seen = {}
        for report in self.reports:
            vid = report.replacement_video_id
            if vid and vid != self.dead_video_id and vid not in seen:
                seen[vid] = Replacement(vid, report.replacement_title, report.replacement_channel)
        return list(seen.values())


def _decision_key(song_id, video_id) -> str:
    return f"{song_id}:{video_id}"


# ==================== inbox ====================

def inbox_available() -> bool:
    # Never create inbox folders under an unmounted mount point (system disk)
    return INBOX_DRIVE_MOUNT is not None and os.path.ismount(INBOX_DRIVE_MOUNT)


def _request_paths(folder: str) -> list[Path]:
    if not inbox_available():
        return []
    directory = INBOX_DIR / folder
    if not directory.is_dir():
        return []
    # Dot files are uploads still being written by the server
    return sorted(p for p in directory.iterdir()
                  if p.is_file() and p.suffix == ".db" and not p.name.startswith("."))


def new_request_paths() -> list[Path]:
    """Request files nobody has looked at yet (new/), oldest name first; []
    when the data drive isn't there."""
    return _request_paths(NEW)


def review_request_paths() -> list[Path]:
    """Request files the automatic pass left to the owner (review/)."""
    return _request_paths(REVIEW)


def count_new_requests() -> int:
    """Request files not settled yet: new/ and review/."""
    try:
        return len(new_request_paths()) + len(review_request_paths())
    except OSError:
        return 0


def _columns(conn, table: str) -> set:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def read_request_file(path: Path) -> RequestFile:
    """Read one request file without changing it. Never raises for a bad file:
    RequestFile.error says what's wrong."""
    request = RequestFile(path=path, package=package_entry(path.name))
    try:
        # immutable: no locks, journal or -shm next to a file we only read
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error as exc:
        request.error = f"can't open: {exc}"
        return request
    try:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        if "request" not in tables:
            request.error = "no request table"
            return request
        rows = conn.execute("SELECT format, created_at, user_name, app FROM request").fetchall()
        if len(rows) != 1:
            request.error = f"request table has {len(rows)} rows, expected 1"
            return request
        request.format, request.created_at, request.user_name, request.app = rows[0]
        request.unknown_tables = sorted(t for t in tables
                                         if t not in KNOWN_TABLES and t not in PLATFORM_TABLES)
        if request.format != PHONE_REQUEST_FORMAT:
            return request
        if DEAD_LINKS_TABLE in tables:
            wanted = ["song_id", "title", "artist", "dead_video_id", "detected_at",
                      "replacement_video_id", "replacement_title", "replacement_channel",
                      "replacement_found_at"]
            have = _columns(conn, DEAD_LINKS_TABLE)
            missing = [c for c in wanted[:5] if c not in have]
            if missing:
                request.error = f"{DEAD_LINKS_TABLE} lacks {', '.join(missing)}"
                return request
            select = ", ".join(c if c in have else "NULL" for c in wanted)
            for row in conn.execute(f"SELECT {select} FROM {DEAD_LINKS_TABLE}"):
                request.dead_links.append(DeadLinkReport(path.name, *row))
        if FIELD_CORRECTIONS_TABLE in tables:
            wanted = ["table_name", "row_id", "column_name", "old_value", "new_value",
                      "song_id", "title", "artist", "corrected_at"]
            missing = [c for c in wanted if c not in _columns(conn, FIELD_CORRECTIONS_TABLE)]
            if missing:
                request.error = f"{FIELD_CORRECTIONS_TABLE} lacks {', '.join(missing)}"
                return request
            for row in conn.execute(f"SELECT {', '.join(wanted)} FROM {FIELD_CORRECTIONS_TABLE}"):
                request.field_corrections.append(FieldCorrection(path.name, *row))
        if FOUND_LINKS_TABLE in tables:
            wanted = ["song_id", "title", "artist", "video_id", "video_title", "video_channel", "found_at"]
            have = _columns(conn, FOUND_LINKS_TABLE)
            missing = [c for c in wanted if c not in have and c not in ("video_title", "video_channel")]
            if missing:
                request.error = f"{FOUND_LINKS_TABLE} lacks {', '.join(missing)}"
                return request
            select = ", ".join(c if c in have else "NULL" for c in wanted)
            for row in conn.execute(f"SELECT {select} FROM {FOUND_LINKS_TABLE}"):
                request.found_links.append(FoundLinkReport(path.name, *row))
        if SONG_REQUESTS_TABLE in tables:
            wanted = ["title", "artist", "album", "year", "duration_s", "youtube_video_id",
                      "spotify_track_id", "source", "requested_at"]
            required = ["title", "source", "requested_at"]
            have = _columns(conn, SONG_REQUESTS_TABLE)
            missing = [c for c in required if c not in have]
            if missing:
                request.error = f"{SONG_REQUESTS_TABLE} lacks {', '.join(missing)}"
                return request
            select = ", ".join(c if c in have else "NULL" for c in wanted)
            for row in conn.execute(f"SELECT {select} FROM {SONG_REQUESTS_TABLE}"):
                request.song_requests.append(SongRequestReport(path.name, *row))
    except sqlite3.Error as exc:
        request.error = f"unreadable: {exc}"
    finally:
        conn.close()
    return request


def read_new_requests() -> list[RequestFile]:
    return [read_request_file(path) for path in new_request_paths()]


def read_review_requests() -> list[RequestFile]:
    return [read_request_file(path) for path in review_request_paths()]


def _move(path: Path, folder: str) -> Path:
    target_dir = INBOX_DIR / folder
    if path.parent == target_dir:
        return path
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    n = 1
    while target.exists():
        target = target_dir / f"{path.stem}.{n}{path.suffix}"
        n += 1
    os.replace(path, target)
    return target


def reject_request_file(request: RequestFile) -> Path:
    flog(f"Inbox request {request.name} rejected: {request.error}")
    return _move(request.path, REJECTED)


# ==================== request packages ====================

def package_files() -> dict:
    """packages.json: request file name -> {"sender", "package", "imported_at"}."""
    if INBOX_DIR is None:
        return {}
    return _load_json(PACKAGES_FILE, {})


def package_entry(name: str) -> dict | None:
    # _move() names a clash "<stem>.1.db"; it's still that package's file
    entry = package_files().get(re.sub(r"\.\d+\.db$", ".db", name))
    return entry if isinstance(entry, dict) else None


def record_package_files(entries: dict) -> None:
    files = package_files()
    files.update(entries)
    _save_json(PACKAGES_FILE, files)


def describe_files(names) -> str:
    """The request files' names for the review, a package's marked with its sender."""
    packages = package_files()
    labels = []
    for name in sorted(names):
        entry = packages.get(re.sub(r"\.\d+\.db$", ".db", name))
        if isinstance(entry, dict):
            labels.append(f"{name} (from {entry.get('sender') or 'no name'}, imported from a package)")
        else:
            labels.append(name)
    return ", ".join(labels)


# ==================== decisions ====================

def _load_json(name: str, default):
    try:
        value = json.loads((INBOX_DIR / name).read_text())
    except (OSError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _save_json(name: str, value) -> None:
    path = INBOX_DIR / name
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_decisions() -> dict:
    return _load_json(DECISIONS_FILE, {})


def save_decision(key: str, entry: dict) -> None:
    decisions = load_decisions()
    decisions[key] = entry
    _save_json(DECISIONS_FILE, decisions)


# ==================== the automatic pass's notes ====================

def save_case_checks(cases: list) -> None:
    """Remember each case's YouTube check (links, and YouTube link
    corrections) and auto_note, for the review. `cases`: link and field
    correction cases."""
    checks = _load_json(CHECKS_FILE, {})
    for case in cases:
        entry = {"note": getattr(case, "auto_note", None)}
        if getattr(case, "checked_at", None):
            entry.update(checked_at=case.checked_at, plays=case.plays)
        if isinstance(case, LinkCase) and case.checked_at:
            entry.update(our_video_id=case.our_video_id, dead_still_plays=case.dead_still_plays)
        checks[case.key] = entry
    _save_json(CHECKS_FILE, checks)


def load_case_checks(cases: list, max_age_hours: float) -> None:
    """Fill auto_note, and a case's YouTube check while it's younger than
    `max_age_hours` (an older one is left unchecked, to be checked again)."""
    checks = _load_json(CHECKS_FILE, {})
    for case in cases:
        entry = checks.get(case.key)
        if not isinstance(entry, dict):
            continue
        case.auto_note = entry.get("note")
        if not hasattr(case, "checked_at") or not entry.get("checked_at"):
            continue
        checked = parse_time(entry["checked_at"])
        if checked is None or (datetime.now().astimezone() - checked).total_seconds() > max_age_hours * 3600:
            continue
        case.checked_at = entry["checked_at"]
        case.plays = entry.get("plays") or {}
        if isinstance(case, LinkCase):
            case.our_video_id = entry.get("our_video_id")
            case.dead_still_plays = entry.get("dead_still_plays")


def add_auto_report(entries: list[dict], shown: bool = False) -> None:
    """Append what the automatic pass changed (each {"text", ...});
    `shown` = the owner has seen it already (the pass ran in the app)."""
    if not entries:
        return
    report = _load_json(REPORT_FILE, [])
    report += [{**entry, "shown": shown} for entry in entries]
    _save_json(REPORT_FILE, report)


def auto_report(unshown_only: bool = False) -> list[dict]:
    return [e for e in _load_json(REPORT_FILE, [])
            if isinstance(e, dict) and not (unshown_only and e.get("shown"))]


def mark_auto_report_shown() -> None:
    report = _load_json(REPORT_FILE, [])
    if any(isinstance(e, dict) and not e.get("shown") for e in report):
        _save_json(REPORT_FILE, [{**e, "shown": True} if isinstance(e, dict) else e for e in report])


def parse_time(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    # A time without an offset is taken as this machine's local time
    return parsed if parsed.tzinfo else parsed.astimezone()


def _kept_after_reports(decision: dict | None, reports: list[DeadLinkReport]) -> bool:
    if not decision or decision.get("decision") != KEEP:
        return False
    decided = parse_time(decision.get("decided_at"))
    detected = [t for t in (parse_time(r.detected_at) for r in reports) if t]
    # A report made after the user kept the link means it may have died since
    return decided is not None and all(t <= decided for t in detected)


# ==================== videos found bad ====================

def merged_song_ids() -> dict:
    """song_id_changes as {old id: new id, or None = deleted}."""
    return {old: new for _, old, new in load_changes()}


def _reason_label(reason: str | None) -> str:
    return BAD_VIDEO_REASONS.get(reason, reason or "found bad before")


class BadVideos:
    """music.db's bad_youtube_videos as read once per pass. A video is bad for
    every song (song_id NULL) or for one song (wrong for it, not broken);
    song_id is kept current through merges by a trigger (migration 0010)."""

    def __init__(self, rows):
        self.gone = {}
        self.wrong = {}
        for row in rows:
            if row.song_id is None:
                self.gone[row.video_id] = row.reason
            else:
                self.wrong.setdefault(row.song_id, {})[row.video_id] = row.reason

    def why(self, video_id: str | None, song_id: int | None = None) -> str | None:
        """Why this video is no good (for this song), as shown, or None."""
        if not video_id:
            return None
        if video_id in self.gone:
            return _reason_label(self.gone[video_id])
        if song_id is not None and video_id in self.wrong.get(song_id, {}):
            return _reason_label(self.wrong[song_id][video_id])
        return None


def load_bad_videos(session) -> BadVideos:
    return BadVideos(session.query(BadYoutubeVideo).all())


def mark_bad_videos(session, video_ids, reason: str, song_id: int | None = None) -> int:
    """Remember these videos as bad (a BAD_VIDEO_REASONS code): for every
    song, or with `song_id` only for that song. A row already there stays.
    The caller commits. Returns how many were added."""
    video_ids = [v for v in dict.fromkeys(video_ids) if v and v != NO_VIDEO_SENTINEL]
    if not video_ids:
        return 0
    query = session.query(BadYoutubeVideo.video_id).filter(BadYoutubeVideo.video_id.in_(video_ids))
    query = query.filter(BadYoutubeVideo.song_id.is_(None) if song_id is None
                         else BadYoutubeVideo.song_id == song_id)
    there = {v for (v,) in query}
    added = [v for v in video_ids if v not in there]
    for video_id in added:
        session.add(BadYoutubeVideo(video_id=video_id, song_id=song_id, reason=reason, marked_at=now_iso()))
    if added:
        mlog(f"Inbox: bad video(s) {', '.join(added)}{f' for song {song_id}' if song_id else ''}: {reason}")
    return len(added)


def unmark_bad_video(session, video_id: str | None, song_id: int | None) -> None:
    """The owner chose or kept this video for this song: it's not bad (for
    every song either - they know better than a check that said it doesn't
    play). The caller commits."""
    if not video_id:
        return
    session.query(BadYoutubeVideo).filter(
        BadYoutubeVideo.video_id == video_id,
        or_(BadYoutubeVideo.song_id.is_(None), BadYoutubeVideo.song_id == song_id),
    ).delete(synchronize_session=False)


# ==================== cases ====================

def resolve_song_id(song_id: int, merged_into: dict) -> int | None:
    """The id this song has now: followed through merges, None if deleted."""
    seen = set()
    while song_id in merged_into and song_id not in seen:
        seen.add(song_id)
        song_id = merged_into[song_id]
        if song_id is None:
            return None
    return song_id


def links_set_by_hand(requests: list[RequestFile], merged_into: dict) -> dict:
    """{current song id: when its newest songs.youtube_video_id correction
    was made (None = unreadable time)} from these files - the phone's user
    set the link by hand, which says more than an older dead link or found
    link report of that song (app handoff musicdatabase-youtube-links-by-hand.md)."""
    newest = {}
    for request in requests:
        if not request.supported:
            continue
        for report in request.field_corrections:
            if report.table_name != "songs" or report.column_name != "youtube_video_id" or report.row_id is None:
                continue
            song_id = resolve_song_id(report.row_id, merged_into)
            if song_id is None:
                continue
            made = parse_time(report.corrected_at)
            if song_id not in newest or (newest[song_id] is not None and (made is None or made > newest[song_id])):
                newest[song_id] = made
    return newest


def superseded_by_hand(set_at: dict, song_id, report_times) -> bool:
    """A link report of this song is older than the link its user set by
    hand (an unreadable time counts as older than the correction)."""
    if song_id not in set_at:
        return False
    corrected = set_at[song_id]
    if corrected is None:
        return True
    return all(t is None or t <= corrected for t in (parse_time(x) for x in report_times))


def collect_dead_link_cases(session, requests: list[RequestFile]) -> list[DeadLinkCase]:
    """Group every supported file's dead link reports by (current song, dead
    video) and settle what needs no answer. Order: first report first."""
    merged_into = merged_song_ids()
    bad_videos = load_bad_videos(session)
    decisions = load_decisions()
    set_by_hand = links_set_by_hand(requests, merged_into)
    cases: dict[tuple, DeadLinkCase] = {}
    for request in requests:
        if not request.supported:
            continue
        for report in request.dead_links:
            song_id = resolve_song_id(report.song_id, merged_into)
            key = (song_id, report.dead_video_id) if song_id is not None else (None, report.song_id, report.dead_video_id)
            case = cases.get(key)
            if case is None:
                case = cases[key] = DeadLinkCase(song_id, report.dead_video_id, [])
            case.reports.append(report)

    for case in cases.values():
        case.song = session.get(Song, case.song_id) if case.song_id is not None else None
        if case.song is None:
            case.status = SONG_DELETED
        elif case.song.youtube_video_id and case.song.youtube_video_id != case.dead_video_id:
            # Fixed already, or "N/A". An empty link stays open: the phone's
            # video may still be worth taking.
            case.status = LINK_ALREADY_CHANGED
        elif _kept_after_reports(decisions.get(case.key), case.reports):
            case.status = ALREADY_KEPT
        elif superseded_by_hand(set_by_hand, case.song_id,
                                [r.replacement_found_at or r.detected_at for r in case.reports]):
            case.status = LINK_SET_BY_HAND
        else:
            case.find_bad(bad_videos)
    return list(cases.values())


def check_link_cases(cases: list[LinkCase], on_case=None) -> dict:
    """Find our own candidate for every case (dead links and found links) and
    check whether the candidates
    play, asking YouTube as little as possible - yt-dlp gets rate-limited and
    then hits a "confirm you're not a bot" wall (2026-10-13):
    1. our matcher's search (yt-dlp), per case - the phone's matcher has less
       to go on (app ADR 0003), so ours is a second opinion;
    2. one Data API call for every id at once (videos_exist_via_api(), no
       bot wall): a "gone" there is final;
    3. yt-dlp only for suggested/newfound ids the API didn't rule out, once
       per id - the API lists some videos YouTube won't play.
    The dead link itself isn't played again; only the API's answer is kept.
    A video found bad before is never our candidate, and what this finds gone
    or not playing goes to bad_youtube_videos - committed here, so the
    phones learn it with the next copy of music.db.
    `on_case(i, case)` is called before each search, for progress. Fills
    checked_at, so save_case_checks() can keep it for the review.
    Returns counts for the screen: {"api": bool, "blocked": n, "marked": n}
    (n videos newly found bad)."""
    from utils.youtube.manage_youtube_playlists import (
        search_video_ytdlp, video_playability, videos_exist_via_api,
    )

    if not cases:
        return {"api": False, "blocked": 0, "marked": 0}
    session = object_session(cases[0].song)
    bad_videos = load_bad_videos(session)
    for i, case in enumerate(cases, 1):
        if on_case:
            on_case(i, case)
        song = case.song
        try:
            found = search_video_ytdlp(
                song_main_artist_label(song), song.title,
                artist_synonyms=artist_match_synonyms(song.artist), language=song.language,
                artist_credits=song_artist_credits(song), title_latin=song.title_latin,
                full_album=is_full_album(song),
            )
        except Exception as exc:
            flog(f"Search for inbox request {song.artist.name} - {song.title} failed: {exc}")
            found = None
        if found == case.dead_video_id or bad_videos.why(found, case.song_id):
            found = None
        case.our_video_id = found

    candidates = [[r.video_id for r in case.replacements] + ([case.our_video_id] if case.our_video_id else [])
                  for case in cases]
    api = videos_exist_via_api(
        [case.dead_video_id for case in cases if case.dead_video_id] + [v for ids in candidates for v in ids])
    known = {}
    blocked = 0
    for case, ids in zip(cases, candidates):
        if api is not None and api.get(case.dead_video_id) is False:
            case.dead_still_plays = False
        for video_id in ids:
            if video_id not in known:
                if api is not None and api.get(video_id) is False:
                    known[video_id] = False
                else:
                    answer = video_playability(video_id)
                    if answer is None:
                        blocked += 1
                        # Listed by the API is the best we know then
                        answer = True if api is not None and api.get(video_id) else None
                    known[video_id] = answer
            case.plays[video_id] = known[video_id]
        case.checked_at = now_iso()

    removed = [v for v, exists in (api or {}).items() if exists is False]
    marked = mark_bad_videos(session, removed, BAD_VIDEO_REMOVED)
    marked += mark_bad_videos(session, [v for v, plays in known.items() if plays is False and v not in removed],
                              BAD_VIDEO_UNPLAYABLE)
    session.commit()
    return {"api": api is not None, "blocked": blocked, "marked": marked}


def apply_dead_link_decision(case: DeadLinkCase, decision: str, video_id: str | None = None,
                             by: str | None = None) -> None:
    """Change the song's link as the user decided and record the answer
    (`by` = "auto" for the automatic pass). The caller commits the music
    session."""
    session = object_session(case.song)
    if decision == USE_VIDEO:
        if not video_id:
            raise ValueError("USE_VIDEO needs a video id")
        case.song.youtube_video_id = video_id
        unmark_bad_video(session, video_id, case.song_id)
    elif decision == NO_VIDEO:
        case.song.youtube_video_id = NO_VIDEO_SENTINEL
        mark_bad_videos(session, [r.video_id for r in case.all_replacements()],
                        BAD_VIDEO_NO_VIDEO_FOR_SONG, case.song_id)
    elif decision != KEEP:
        raise ValueError(f"Unknown decision {decision!r}")
    if decision == KEEP:
        unmark_bad_video(session, case.dead_video_id, case.song_id)
    else:
        mark_bad_videos(session, [case.dead_video_id], BAD_VIDEO_REPLACED_DEAD_LINK)
    save_decision(case.key, {
        "decision": decision,
        "video_id": video_id,
        "decided_at": now_iso(),
        "files": sorted({r.file_name for r in case.reports}),
        **({"by": by} if by else {}),
    })
    case.status = DECIDED
    mlog(f"Inbox request: song {case.song_id} dead link {case.dead_video_id} -> {decision} {video_id or ''}")


def finish_request_files(requests: list[RequestFile], cases: list,
                         left_to: str | None = None) -> tuple[list, list]:
    """Move every file with nothing left to answer to processed/, the rest
    to `left_to` (REVIEW for the automatic pass) or nowhere. `cases`: every
    case of every type (anything with `.reports` and `.pending`).
    Returns (moved, left) as RequestFiles."""
    open_reports = {id(report) for case in cases if case.pending for report in case.reports}
    moved, left = [], []
    for request in requests:
        if request.error is not None:
            continue
        done = (request.supported and not request.unknown_tables
                and not any(id(report) in open_reports
                            for report in request.dead_links + request.field_corrections
                            + request.found_links + request.song_requests))
        if done:
            _move(request.path, PROCESSED)
            moved.append(request)
        else:
            if left_to:
                request.path = _move(request.path, left_to)
            left.append(request)
    return moved, left
