"""
Songs Leina Radio asked to have added (the `song_requests` table of a request
file, app handoff docs/handoffs/musicdatabase-song-requests.md, answer
docs/handoffs/song-requests-for-musicdatabaseapp.md): a track the phone
played that music.db doesn't have - a YouTube video, a Spotify track or a file
on the phone - by its title and artist as the phone showed them. Nothing here
asks anything (ADR-0001); the review is utils/ui/inbox_request_review.py.

Adding a song is never automatic (the owner's decision, 2026-10-18): the
owner adds it (through the import, run_import_batch(), which looks it up like
any other import), skips it for later, or rejects it. A rejected song goes to
music.db's rejected_song_requests (migration 0015), which the phone reads, so
it doesn't ask for that song again.

Cases: one per song, by normalized artist + title, from every report of
every file. Settled without asking (the automatic pass too):
- the song is in the database already (the phone's music.db was older, or
  the phone didn't recognise it) - LocalSongMatcher, or its YouTube video is
  a song's link;
- it was rejected before (a rejected_song_requests row with the same artist +
  title, YouTube video or Spotify track).

A request may carry a YouTube video's channel as the artist and the whole
video title as the title ("familyboombox" / "Бумбокс - Твiй на 100%"; Leina
Radio's YouTube search results did that), or a label's channel ("Fueled By
Ramen" / "Travie McCoy: Billionaire ft. Bruno Mars"). When the title is
"<artist> - <title>" or "<artist>: <title>" and the request's artist is none
the database knows (or none at all), the case suggests the split
(`suggested`), the artist from the title known to the database or not; when
the request's artist is a known one, only if the title names that same
artist (a synonym, as with familyboombox). The review offers it first, and
the "already in the database" check uses it too.
"""

import re
from dataclasses import dataclass

from config.constants import SONG_REQUEST_COLON_ARTIST_MAX_WORDS
from utils.common.debug import mlog
from utils.common.normalizer import normalize
from utils.database.datatables import RejectedSongRequest, Song
from utils.database.inbox_requests import (
    DECIDED,
    OPEN,
    RequestFile,
    SongRequestReport,
    now_iso,
    parse_time,
    save_decision,
)
from utils.database.song_duplicates import split_feat_credits

# Settled statuses of their own
ALREADY_IN_DATABASE = "already in the database"
REJECTED_BEFORE = "rejected before"

# Answers
ADD = "added"
REJECT = "rejected"

# What separates the artist from the title inside a video's title
_TITLE_ARTIST_SEPARATOR = re.compile(r"\s+[-–—]\s+")
# ... or, when there's no dash, a colon right after the artist
_TITLE_ARTIST_COLON = re.compile(r":\s+")

SOURCE_LABELS = {"youtube": "YouTube", "spotify": "Spotify", "phone_file": "a file on the phone"}


def song_key(artist: str | None, title: str | None) -> str:
    return f"{normalize(artist or '')}|{normalize(title or '')}"


@dataclass
class SongRequestCase:
    """Every request for one song (same artist and title)."""
    reports: list
    status: str = OPEN
    existing: Song | None = None  # the song it already is, for ALREADY_IN_DATABASE
    auto_note: str | None = None  # why the automatic pass left it to the owner
    suggested: tuple | None = None  # (artist, title) read out of the title, see the module docstring

    @property
    def pending(self) -> bool:
        return self.status == OPEN

    @property
    def key(self) -> str:
        return f"song:{song_key(self.latest.artist, self.latest.title)}"

    @property
    def latest(self) -> SongRequestReport:
        """The newest report (its spelling is shown and suggested)."""
        def when(report):
            t = parse_time(report.requested_at)
            return t.timestamp() if t else float("-inf")
        return max(self.reports, key=when)

    def _first(self, attribute):
        """The newest report's value, else any report's."""
        for report in [self.latest, *self.reports]:
            value = getattr(report, attribute)
            if value not in (None, ""):
                return value
        return None

    @property
    def youtube_video_ids(self) -> list[str]:
        return list(dict.fromkeys(r.youtube_video_id for r in self.reports if r.youtube_video_id))

    @property
    def spotify_track_ids(self) -> list[str]:
        return list(dict.fromkeys(r.spotify_track_id for r in self.reports if r.spotify_track_id))

    @property
    def sources(self) -> list[str]:
        return list(dict.fromkeys(SOURCE_LABELS.get(r.source, r.source) for r in self.reports))

    def metadata(self, artist: str | None = None, title: str | None = None) -> dict:
        """An import_engine metadata dict (run_import_batch()); `artist` /
        `title` as the owner corrected them, else the request's. A
        featured-artist credit leaves the title for `feat_artists`, which the
        import credits on the new song ("Billionaire ft. Bruno Mars" ->
        "Billionaire", Bruno Mars as feat)."""
        title, feat_artists = split_feat_credits(title if title is not None else self.latest.title or "")
        return {
            "artist_name": artist if artist is not None else self.latest.artist,
            "title": title or None,
            "feat_artists": feat_artists,
            "album": self._first("album"),
            "year": self._first("year"),
            "language": "Unknown",
            "origin": None,
            "youtube_video_id": self.youtube_video_ids[0] if self.youtube_video_ids else None,
            "duration_s": self._first("duration_s"),
        }


def _rejected_rows(session) -> tuple[set, set, set]:
    """(artist+title keys, YouTube ids, Spotify ids) of rejected_song_requests."""
    keys, videos, tracks = set(), set(), set()
    for row in session.query(RejectedSongRequest).all():
        keys.add(song_key(row.artist, row.title))
        if row.youtube_video_id:
            videos.add(row.youtube_video_id)
        if row.spotify_track_id:
            tracks.add(row.spotify_track_id)
    return keys, videos, tracks


def suggested_artist_title(artist: str | None, title: str | None, matcher) -> tuple | None:
    """(artist, title) when `title` is "<artist> - <title>" (or "<artist>:
    <title>", the artist part short) and `artist` is one the database doesn't
    know (a YouTube channel, a label) or the same artist as the title's:
    "familyboombox" (Бумбокс's synonym) / "Бумбокс - Твiй на 100%" ->
    ("Бумбокс", "Твiй на 100%"); "Fueled By Ramen" / "Travie McCoy:
    Billionaire ft. Bruno Mars" -> ("Travie McCoy", "Billionaire ft. Bruno
    Mars"). The title's artist is given as the database spells it when it
    knows it. None otherwise - a known artist's channel uploading another
    artist's song is left as asked."""
    parts = _split_title(title or "")
    if parts is None:
        return None
    in_title = matcher.artist(parts[0])
    asked = matcher.artist(artist) if artist else None
    if asked is not None and (in_title is None or asked.id != in_title.id):
        return None
    return (in_title.name if in_title is not None else parts[0]), parts[1]


def _split_title(title: str) -> tuple[str, str] | None:
    """(artist part, title part) of a video title, by " - " first, else by
    ": " after at most SONG_REQUEST_COLON_ARTIST_MAX_WORDS words."""
    parts = _TITLE_ARTIST_SEPARATOR.split(title, maxsplit=1)
    if len(parts) < 2:
        parts = _TITLE_ARTIST_COLON.split(title, maxsplit=1)
        if len(parts) < 2 or len(parts[0].split()) > SONG_REQUEST_COLON_ARTIST_MAX_WORDS:
            return None
    left, right = parts[0].strip(), parts[1].strip()
    return (left, right) if left and right else None


def _song_in_database(session, case: SongRequestCase, matcher) -> Song | None:
    for video_id in case.youtube_video_ids:
        song = session.query(Song).filter(Song.youtube_video_id == video_id).first()
        if song is not None:
            return song
    metadata = case.metadata()
    if case.suggested:
        song = matcher.match(case.metadata(*case.suggested))
        if song is not None:
            return song
    if not metadata["artist_name"]:
        return None
    return matcher.match(metadata)


def collect_song_request_cases(session, requests: list[RequestFile], matcher=None) -> list[SongRequestCase]:
    """Group every supported file's song requests by song and settle what
    needs no answer. Order: first report first. `matcher`: a
    LocalSongMatcher to use (tests); made here otherwise."""
    cases: dict[str, SongRequestCase] = {}
    for request in requests:
        if not request.supported:
            continue
        for report in request.song_requests:
            key = song_key(report.artist, report.title)
            cases.setdefault(key, SongRequestCase([])).reports.append(report)
    if not cases:
        return []

    if matcher is None:
        from utils.discoveries.import_engine import LocalSongMatcher
        matcher = LocalSongMatcher(session)
    rejected_keys, rejected_videos, rejected_tracks = _rejected_rows(session)
    for key, case in cases.items():
        case.suggested = suggested_artist_title(case.latest.artist, case.latest.title, matcher)
        if (key in rejected_keys or (case.suggested and song_key(*case.suggested) in rejected_keys)
                or set(case.youtube_video_ids) & rejected_videos
                or set(case.spotify_track_ids) & rejected_tracks):
            case.status = REJECTED_BEFORE
            continue
        case.existing = _song_in_database(session, case, matcher)
        if case.existing is not None:
            case.status = ALREADY_IN_DATABASE
    return list(cases.values())


def reject_song_request(session, case: SongRequestCase) -> None:
    """The owner doesn't want the song: published in rejected_song_requests,
    one row per distinct way the phone named it. The caller commits."""
    seen = set()
    for report in case.reports:
        row = (report.artist, report.title, report.youtube_video_id, report.spotify_track_id)
        if row in seen:
            continue
        seen.add(row)
        session.add(RejectedSongRequest(artist=report.artist, title=report.title,
                                        youtube_video_id=report.youtube_video_id,
                                        spotify_track_id=report.spotify_track_id, rejected_at=now_iso()))
    _decided(case, REJECT, session=session)


def mark_song_added(case: SongRequestCase, artist: str | None, title: str) -> None:
    """The song went through the import (added, or found there after all),
    as `artist` - `title`."""
    _decided(case, ADD, added_as={"artist": artist, "title": title})


def _decided(case: SongRequestCase, decision: str, session=None, **extra) -> None:
    save_decision(case.key, {
        "decision": decision,
        "artist": case.latest.artist,
        "title": case.latest.title,
        "decided_at": now_iso(),
        "files": sorted({r.file_name for r in case.reports}),
        **extra,
    }, session)
    case.status = DECIDED
    mlog(f"Inbox request: song {case.latest.artist} - {case.latest.title} -> {decision}")

