"""
Links the phone found for songs without one (the `found_youtube_links` table
of a request file, app handoff docs/handoffs/musicdatabase-found-youtube-links.md,
answer docs/handoffs/found-links-for-musicdatabaseapp.md): music.db had no
link for a song (NULL or empty, not "N/A"), the phone searched YouTube with
its own lite matcher and plays what it found. Reading the files, the YouTube
check and the videos found bad (music.db's bad_youtube_videos) are shared with dead link reports (inbox_requests.py);
nothing here asks anything (ADR-0001).

Cases: one per song (song_id followed through song_id_changes), from every
report of every file. The phone reports each (song, video) once but may later
find another video for the song (the first one stopped playing, or the user
searched again), and two phones may disagree - every video is offered, the
newest `found_at` first (the phone's current pick). Settled without asking:
- the song was deleted;
- the song has a link or "N/A" now;
- every video named was found bad before (bad_youtube_videos) - turned down
  without asking YouTube again;
- a newer youtube_video_id correction of the song (its user set the link by
  hand) is in the same batch - the correction is asked about instead;
- (automatic pass) none of the videos plays here.
The automatic pass saves a link only when our own search found one of the
phone's videos and it plays here, as for dead link replacements.
"""

from dataclasses import dataclass

from sqlalchemy.orm import object_session

from config.constants import BAD_VIDEO_NO_VIDEO_FOR_SONG, BAD_VIDEO_WRONG_FOR_SONG
from utils.common.debug import mlog
from utils.database.datatables import Song
from utils.database.inbox_requests import (
    DECIDED,
    LINK_ALREADY_CHANGED,
    LINK_SET_BY_HAND,
    NO_VIDEO,
    OPEN,
    SONG_DELETED,
    USE_VIDEO,
    LinkCase,
    Replacement,
    RequestFile,
    load_bad_videos,
    mark_bad_videos,
    merged_song_ids,
    now_iso,
    parse_time,
    links_set_by_hand,
    resolve_song_id,
    save_decision,
    superseded_by_hand,
    unmark_bad_video,
)
from utils.youtube.manage_youtube_playlists import NO_VIDEO_SENTINEL

# Settled statuses of their own
ONLY_BAD_VIDEOS = "only videos found bad before"
NONE_PLAYS = "the video doesn't play here"

# Answer: the phone's videos are wrong for the song; the song stays without a link
REJECTED = "rejected"


@dataclass
class FoundLinkCase(LinkCase):
    """Every video the phone found for one song that had no link."""

    @property
    def key(self) -> str:
        return f"found:{self.song_id}"

    def all_replacements(self) -> list[Replacement]:
        """Newest found_at first; a report without a readable time last."""
        def found(report):
            when = parse_time(report.found_at)
            return when.timestamp() if when else float("-inf")

        seen = {}
        for report in sorted(self.reports, key=found, reverse=True):
            if report.video_id and report.video_id not in seen:
                seen[report.video_id] = Replacement(report.video_id, report.video_title, report.video_channel)
        return list(seen.values())


def collect_found_link_cases(session, requests: list[RequestFile]) -> list[FoundLinkCase]:
    """Group every supported file's found links by current song and settle
    what needs no answer. Order: first report first."""
    merged_into = merged_song_ids()
    bad_videos = load_bad_videos(session)
    set_by_hand = links_set_by_hand(requests, merged_into)
    cases: dict = {}
    for request in requests:
        if not request.supported:
            continue
        for report in request.found_links:
            song_id = resolve_song_id(report.song_id, merged_into)
            key = song_id if song_id is not None else ("deleted", report.song_id)
            case = cases.get(key)
            if case is None:
                case = cases[key] = FoundLinkCase(song_id, None, [])
            case.reports.append(report)

    for case in cases.values():
        case.song = session.get(Song, case.song_id) if case.song_id is not None else None
        if case.song is None:
            case.status = SONG_DELETED
        elif case.song.youtube_video_id:
            case.status = LINK_ALREADY_CHANGED
        elif superseded_by_hand(set_by_hand, case.song_id, [r.found_at for r in case.reports]):
            case.status = LINK_SET_BY_HAND
        else:
            case.find_bad(bad_videos)
            if not case.replacements:
                case.status = ONLY_BAD_VIDEOS
    return list(cases.values())


def settle_if_none_plays(case: FoundLinkCase) -> bool:
    """After the check: when none of the phone's videos plays here (they are
    in bad_youtube_videos now), settle the case - the phone finds another video
    itself and reports that. True when settled."""
    ids = [r.video_id for r in case.replacements]
    if case.checked_at and ids and all(case.plays.get(v) is False for v in ids):
        case.status = NONE_PLAYS
        return True
    return False


def apply_found_link_decision(case: FoundLinkCase, decision: str, video_id: str | None = None,
                              by: str | None = None) -> None:
    """Set the song's link as decided and record the answer (`by` = "auto"
    for the automatic pass). The caller commits the music session."""
    phone_ids = [r.video_id for r in case.all_replacements()]
    session = object_session(case.song)
    if decision == USE_VIDEO:
        if not video_id:
            raise ValueError("USE_VIDEO needs a video id")
        case.song.youtube_video_id = video_id
        unmark_bad_video(session, video_id, case.song_id)
    elif decision == NO_VIDEO:
        case.song.youtube_video_id = NO_VIDEO_SENTINEL
        mark_bad_videos(session, phone_ids, BAD_VIDEO_NO_VIDEO_FOR_SONG, case.song_id)
    elif decision == REJECTED:
        mark_bad_videos(session, phone_ids, BAD_VIDEO_WRONG_FOR_SONG, case.song_id)
    else:
        raise ValueError(f"Unknown decision {decision!r}")
    named = video_id or (phone_ids[0] if phone_ids else "-")
    save_decision(f"{case.key}:{named}", {
        "decision": decision,
        "video_id": video_id,
        "phone_video_ids": phone_ids,
        "decided_at": now_iso(),
        "files": sorted({r.file_name for r in case.reports}),
        **({"by": by} if by else {}),
    }, session)
    case.status = DECIDED
    mlog(f"Inbox request: song {case.song_id} found link(s) {', '.join(phone_ids)} -> {decision} {video_id or ''}")

