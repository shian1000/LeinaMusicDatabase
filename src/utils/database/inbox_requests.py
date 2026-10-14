"""
Requests from the MusicDatabaseApp phone client: reading the inbox the
sharing server fills, deciding what is still worth asking about, and
applying the user's answers. The review screen is
utils/ui/inbox_request_review.py (Manage database -> "Resolve new requests");
nothing here asks anything, so a later intake can reuse it (ADR-0001).

Inbox (Settings.phone_inbox_dir, on T7):
- new/        - what the sharing server stored (PUT /inbox/<name>.db); counts
                towards its size limit, so processing frees room for more.
- processed/  - files whose every row has been settled; kept, not deleted.
                The server answers a resend of one of these with 200.
- rejected/   - files that aren't readable request files.
- decisions.json - every answer given, keyed "<song id>:<dead video id>".
                Only "kept" answers are looked up again: they leave the
                database unchanged, so without the record the same report
                (from a file still waiting on a skipped row, or another
                phone) would be asked about again.

One request file is one SQLite file with a one-row `request` table and one
table per request type (app ADR 0005; format described in the app's
docs/handoffs/musicdatabase-outbox.md). Known types: dead_youtube_links and
field_corrections (handled in inbox_field_corrections.py). A file with a
table this version doesn't know stays in new/, so a later version can still
handle that part.

A dead link report is settled without asking when:
- its song was deleted since the phone downloaded music.db;
- songs.youtube_video_id already holds another link or "N/A";
- the user kept that link after the report was made.
song_id follows merges through song_id_changes. Reports of the same
(song, dead video) from several files are asked about once.
"""

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from config.constants import PHONE_REQUEST_FORMAT
from settings import Settings
from utils.common.debug import flog, mlog
from utils.common.normalizer import normalize
from utils.database.datatables import Song
from utils.database.song_artists import song_artist_credits, song_main_artist_label
from utils.database.song_references import load_changes
from utils.youtube.manage_youtube_playlists import NO_VIDEO_SENTINEL

INBOX_DIR = Path(str(Settings.phone_inbox_dir))
INBOX_DRIVE_MOUNT = Path(str(Settings.sharing_drive_mount))
NEW = "new"
PROCESSED = "processed"
REJECTED = "rejected"
DECISIONS_FILE = "decisions.json"

DEAD_LINKS_TABLE = "dead_youtube_links"
FIELD_CORRECTIONS_TABLE = "field_corrections"
KNOWN_TABLES = {"request", DEAD_LINKS_TABLE, FIELD_CORRECTIONS_TABLE}
# Android's SQLiteDatabase adds this to every file it creates (just the locale) -
# not a request, so it never keeps a file in the inbox
PLATFORM_TABLES = {"android_metadata"}

# Case statuses: OPEN needs an answer; the others are settled
OPEN = "open"
SONG_DELETED = "song deleted"
LINK_ALREADY_CHANGED = "link already changed"
ALREADY_KEPT = "link kept earlier"
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
class RequestFile:
    path: Path
    format: int | None = None
    created_at: str | None = None
    user_name: str | None = None
    app: str | None = None
    dead_links: list[DeadLinkReport] = field(default_factory=list)
    field_corrections: list[FieldCorrection] = field(default_factory=list)
    unknown_tables: list[str] = field(default_factory=list)
    error: str | None = None  # not a readable request file

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def supported(self) -> bool:
        return self.error is None and self.format == PHONE_REQUEST_FORMAT


@dataclass
class Replacement:
    video_id: str
    title: str | None = None
    channel: str | None = None


@dataclass
class DeadLinkCase:
    """One (song, dead video) pair, from however many reports."""
    song_id: int | None  # after following merges; None = song deleted
    dead_video_id: str
    reports: list[DeadLinkReport]
    song: Song | None = None
    status: str = OPEN
    # Filled by check_dead_link_cases() (slow: YouTube)
    # False = the Data API says the dead link is gone; None = not rechecked
    # (the request already says it's dead - one request fewer to YouTube)
    dead_still_plays: bool | None = None
    # Suggested and newfound videos: True plays / False gone / None unknown
    plays: dict = field(default_factory=dict)
    our_video_id: str | None = None

    @property
    def key(self) -> str:
        return _decision_key(self.song_id, self.dead_video_id)

    @property
    def pending(self) -> bool:
        """Keeps its files in new/."""
        return self.status == OPEN

    @property
    def replacements(self) -> list[Replacement]:
        seen = {}
        for report in self.reports:
            vid = report.replacement_video_id
            if vid and vid != self.dead_video_id and vid not in seen:
                seen[vid] = Replacement(vid, report.replacement_title, report.replacement_channel)
        return list(seen.values())

    @property
    def title_differs(self) -> bool:
        """The phone showed another title for this id (e.g. renamed, or merged into another song)."""
        if self.song is None:
            return False
        return any(normalize(r.title or "") != normalize(self.song.title or "") for r in self.reports)


def _decision_key(song_id, video_id) -> str:
    return f"{song_id}:{video_id}"


# ==================== inbox ====================

def inbox_available() -> bool:
    # Never create inbox folders under an unmounted mount point (system disk)
    return os.path.ismount(INBOX_DRIVE_MOUNT)


def new_request_paths() -> list[Path]:
    """Request files waiting in new/, oldest name first; [] when T7 isn't there."""
    new_dir = INBOX_DIR / NEW
    if not inbox_available() or not new_dir.is_dir():
        return []
    # Dot files are uploads still being written by the server
    return sorted(p for p in new_dir.iterdir()
                  if p.is_file() and p.suffix == ".db" and not p.name.startswith("."))


def count_new_requests() -> int:
    try:
        return len(new_request_paths())
    except OSError:
        return 0


def _columns(conn, table: str) -> set:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def read_request_file(path: Path) -> RequestFile:
    """Read one request file without changing it. Never raises for a bad file:
    RequestFile.error says what's wrong."""
    request = RequestFile(path=path)
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
    except sqlite3.Error as exc:
        request.error = f"unreadable: {exc}"
    finally:
        conn.close()
    return request


def read_new_requests() -> list[RequestFile]:
    return [read_request_file(path) for path in new_request_paths()]


def _move(path: Path, folder: str) -> Path:
    target_dir = INBOX_DIR / folder
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


# ==================== decisions ====================

def load_decisions() -> dict:
    try:
        return json.loads((INBOX_DIR / DECISIONS_FILE).read_text())
    except (OSError, ValueError):
        return {}


def save_decision(key: str, entry: dict) -> None:
    decisions = load_decisions()
    decisions[key] = entry
    path = INBOX_DIR / DECISIONS_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(decisions, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


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


def collect_dead_link_cases(session, requests: list[RequestFile]) -> list[DeadLinkCase]:
    """Group every supported file's dead link reports by (current song, dead
    video) and settle what needs no answer. Order: first report first."""
    merged_into = {old: new for _, old, new in load_changes()}
    decisions = load_decisions()
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
    return list(cases.values())


def check_dead_link_cases(cases: list[DeadLinkCase], on_case=None) -> dict:
    """Find our own candidate for every case and check whether the candidates
    play, asking YouTube as little as possible - yt-dlp gets rate-limited and
    then hits a "confirm you're not a bot" wall (2026-10-13):
    1. our matcher's search (yt-dlp), per case - the phone's matcher has less
       to go on (app ADR 0003), so ours is a second opinion;
    2. one Data API call for every id at once (videos_exist_via_api(), no
       bot wall): a "gone" there is final;
    3. yt-dlp only for suggested/newfound ids the API didn't rule out, once
       per id - the API lists some videos YouTube won't play.
    The dead link itself isn't played again; only the API's answer is kept.
    `on_case(i, case)` is called before each search, for progress.
    Returns counts for the screen: {"api": bool, "blocked": n}."""
    from utils.youtube.manage_youtube_playlists import (
        search_video_ytdlp, video_playability, videos_exist_via_api,
    )

    for i, case in enumerate(cases, 1):
        if on_case:
            on_case(i, case)
        song = case.song
        try:
            found = search_video_ytdlp(
                song_main_artist_label(song), song.title,
                artist_synonyms=song.artist.synonyms, language=song.language,
                artist_credits=song_artist_credits(song),
            )
        except Exception as exc:
            flog(f"Search for inbox request {song.artist.name} - {song.title} failed: {exc}")
            found = None
        case.our_video_id = found if found != case.dead_video_id else None

    candidates = [[r.video_id for r in case.replacements] + ([case.our_video_id] if case.our_video_id else [])
                  for case in cases]
    api = videos_exist_via_api(
        [case.dead_video_id for case in cases] + [v for ids in candidates for v in ids])
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
    return {"api": api is not None, "blocked": blocked}


def apply_dead_link_decision(case: DeadLinkCase, decision: str, video_id: str | None = None) -> None:
    """Change the song's link as the user decided and record the answer.
    The caller commits the music session."""
    if decision == USE_VIDEO:
        if not video_id:
            raise ValueError("USE_VIDEO needs a video id")
        case.song.youtube_video_id = video_id
    elif decision == NO_VIDEO:
        case.song.youtube_video_id = NO_VIDEO_SENTINEL
    elif decision != KEEP:
        raise ValueError(f"Unknown decision {decision!r}")
    save_decision(case.key, {
        "decision": decision,
        "video_id": video_id,
        "decided_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": sorted({r.file_name for r in case.reports}),
    })
    case.status = DECIDED
    mlog(f"Inbox request: song {case.song_id} dead link {case.dead_video_id} -> {decision} {video_id or ''}")


def finish_request_files(requests: list[RequestFile], cases: list) -> tuple[list, list]:
    """Move every file with nothing left to answer to processed/. `cases`:
    every case of every type (anything with `.reports` and `.pending`).
    Returns (moved, kept in new/) as RequestFiles."""
    open_reports = {id(report) for case in cases if case.pending for report in case.reports}
    moved, left = [], []
    for request in requests:
        if request.error is not None:
            continue
        done = (request.supported and not request.unknown_tables
                and not any(id(report) in open_reports
                            for report in request.dead_links + request.field_corrections))
        if done:
            _move(request.path, PROCESSED)
            moved.append(request)
        else:
            left.append(request)
    return moved, left
