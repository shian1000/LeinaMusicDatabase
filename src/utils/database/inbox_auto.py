"""
The automatic pass over new requests from the phone: settles on its own what
it safely can, and leaves the rest to the owner's review (Manage database ->
"Resolve new requests", utils/ui/inbox_request_review.py). Run every
INBOX_AUTO_INTERVAL by a systemd user timer (auto_resolve_requests.py at the
repo root) while the app isn't open, and by the review itself before asking
anything. Nothing here asks anything (ADR-0001).

What it does with the files in new/:
- an unreadable file goes to rejected/;
- what needs no answer is settled as in the review (song gone, link already
  changed, value already there, ...);
- a dead YouTube link is replaced when the phone's suggestion and our own
  search found the same video and it plays here (check_link_cases());
- a link the phone found for a song without one is saved by the same rule;
  when none of its videos plays here it's settled (the phone looks again);
- a field correction is saved when its newest report was sent by a logged-in
  user with the PERMISSION_APPLY_CORRECTIONS permission and nothing about it
  needs a judgement: the value is storable, the database still holds what
  the phone had, no other report asked for another value, the song still
  has that title / artist / album, and saving would merge nothing (another
  artist of that name, an album twin) nor leave the choice of renaming a
  whole album; the song's artist (songs.artist_id) only goes to an artist
  that exists, alone with that name and not on the song yet, and only when
  the old artist keeps other songs (no new artist, no emptied one); a YouTube link correction (songs.youtube_video_id /
  music_video_youtube_id) also needs its video to play here and not to be
  one found bad before for the song ("N/A" and empty need no check);
- a song Leina Radio asked to have added is never added here (the owner
  approves each one); only one already in the database or rejected before is
  settled;
- every case left over gets an auto_note (why), and its YouTube check is
  kept in auto_checks.json, so the review shows both without searching
  YouTube again;
- a file with nothing left goes to processed/, the rest to review/.
What it changed goes to auto_report.json; main.py shows the new entries at
the next start.
"""

from dataclasses import dataclass, field
from pathlib import Path

from settings import Settings
from utils.common.debug import mlog
from utils.database.account_auth import PERMISSION_APPLY_CORRECTIONS, user_permissions
from utils.database.inbox_field_corrections import (
    ACCEPT,
    OPEN as FIELD_OPEN,
    album_title_twin,
    apply_field_correction,
    artist_choices,
    artist_left_empty,
    artist_twins,
    check_correction_videos,
    collect_field_correction_cases,
    latin_form_to_check,
    rename_looks_like_other_artist,
    song_album_to_rename,
)
from utils.database.inbox_found_links import (
    apply_found_link_decision,
    collect_found_link_cases,
    settle_if_none_plays,
)
from utils.database.inbox_requests import (
    LINK_ALREADY_CHANGED,
    OPEN,
    REVIEW,
    USE_VIDEO,
    RequestFile,
    add_auto_report,
    apply_dead_link_decision,
    check_link_cases,
    collect_dead_link_cases,
    finish_request_files,
    now_iso,
    reject_request_file,
    save_case_checks,
)
from utils.database.inbox_song_requests import collect_song_request_cases
from utils.database.song_artists import additional_artist_role, song_artist_label

AUTO = "auto"


@dataclass
class AutoResult:
    rejected: list = field(default_factory=list)  # RequestFiles
    changes: list = field(default_factory=list)  # auto_report entries
    to_review: int = 0  # cases left to the owner
    moved: list = field(default_factory=list)  # files done (processed/)
    left: list = field(default_factory=list)  # files in review/
    check_stats: dict = field(default_factory=dict)  # check_link_cases()'s


def _song_label(song, report) -> str:
    if song is not None:
        return f"{song_artist_label(song)} - {song.title}"
    return f"{report.artist} - {report.title}"


def _shown(value) -> str:
    return "empty" if value is None or value == "" else f'"{value}"' if isinstance(value, str) else str(value)


def link_verdict(case) -> tuple[str | None, str | None]:
    """(video id to put in, None) when the pass may set the link (a dead
    link's replacement or a found link), else (None, why not)."""
    if not case.checked_at:
        return None, "not checked on YouTube"
    suggested = [r.video_id for r in case.replacements]
    if not suggested:
        if case.bad:
            return None, "Leina Radio suggested only videos found bad before"
        return None, ("Leina Radio suggested no video" if not case.our_video_id
                      else "Leina Radio suggested no video, only our search found one")
    if not case.our_video_id:
        return None, "our search found nothing to compare Leina Radio's suggestion with"
    if case.our_video_id not in suggested:
        return None, "Leina Radio's suggestion and our search found different videos"
    plays = case.plays.get(case.our_video_id)
    if plays is not True:
        return None, ("the video doesn't play here" if plays is False
                      else "couldn't check whether the video plays (YouTube blocked it)")
    return case.our_video_id, None


def field_correction_verdict(session, case, sender: str | None, permitted: bool) -> str | None:
    """None when the pass may save the correction, else why not."""
    if sender is None:
        return "sent without logging in"
    if not permitted:
        return f"{sender} has no permission to save corrections without review"
    if case.error:
        return f"the value can't be stored: {case.error}"
    if case.changed_since:
        return "the database's value changed since Leina Radio's copy"
    if case.earlier_values:
        return "other reports asked for other values"
    if case.song is None:
        return "the song it was corrected from is gone"
    if case.title_differs:
        return "the song has another title now"
    if case.not_on_song:
        return "that artist / album isn't the song's any more"
    if case.is_artist:
        matches = artist_choices(session, case, case.new_value)
        if not matches:
            return "no artist of that name yet (a new one would be made)"
        if len(matches) > 1:
            return "several artists have that name"
        if additional_artist_role(case.row, matches[0]):
            return "that artist is already credited on the song"
        if artist_left_empty(session, case):
            return "the song's artist would be left without songs"
    if artist_twins(session, case, case.new_value):
        return "another artist already has that name (a merge?)"
    if rename_looks_like_other_artist(case, case.new_value):
        return "the new name doesn't look like a spelling fix (maybe only the song is another artist's)"
    if album_title_twin(session, case, case.new_value) is not None:
        return "the artist already has an album of that title (saving merges the two)"
    if song_album_to_rename(case) is not None:
        return "its whole album could be renamed instead"
    if latin_form_to_check(case, case.new_value):
        return "it has a Latin form that may need changing too"
    if case.video_id:
        if case.bad:
            return f"the video was found bad before: {case.bad}"
        if not case.checked_at:
            return "not checked on YouTube"
        plays = case.plays.get(case.video_id)
        if plays is not True:
            return ("the video doesn't play here" if plays is False
                    else "couldn't check whether the video plays (YouTube blocked it)")
    return None


def _current_shown(case):
    """The field's value as the report shows it: the song's artist by name, not id."""
    return case.row.artist.name if case.is_artist else case.current


def auto_resolve(session, requests: list[RequestFile], on_case=None, shown: bool = False) -> AutoResult:
    """The automatic pass over `requests` (read from new/). Commits
    `session` (music.db) after each change and adds the changes to
    auto_report.json (`shown` = the owner is watching, the app ran it).
    `on_case(i, n, case)` is called before each YouTube search, for progress."""
    result = AutoResult()
    for request in [r for r in requests if r.error]:
        reject_request_file(request)
        result.rejected.append(request)
    requests = [r for r in requests if not r.error]
    senders = {r.name: r.sender for r in requests}

    dead_cases = collect_dead_link_cases(session, requests)
    found_cases = collect_found_link_cases(session, requests)
    open_dead = [c for c in dead_cases if c.status == OPEN]
    open_found = [c for c in found_cases if c.status == OPEN]
    to_check = open_dead + open_found
    if to_check:
        # One batch, so the Data API is asked once for everything
        result.check_stats = check_link_cases(
            to_check, on_case=(lambda i, case: on_case(i, len(to_check), case)) if on_case else None)
    for case in open_dead:
        video_id, case.auto_note = link_verdict(case)
        if video_id is None:
            continue
        apply_dead_link_decision(case, USE_VIDEO, video_id, by=AUTO)
        session.commit()
        result.changes.append({
            "at": now_iso(), "kind": "dead_link", "song_id": case.song_id,
            "text": f"{_song_label(case.song, case.reports[0])}: YouTube link {case.dead_video_id} -> {video_id} "
                    "(Leina Radio's suggestion; our search found the same video)",
            "files": sorted({r.file_name for r in case.reports}),
        })

    for case in open_found:
        if case.song.youtube_video_id:
            # A dead link report for the same song was just settled above
            case.status = LINK_ALREADY_CHANGED
            continue
        if settle_if_none_plays(case):
            continue
        video_id, case.auto_note = link_verdict(case)
        if video_id is None:
            continue
        apply_found_link_decision(case, USE_VIDEO, video_id, by=AUTO)
        session.commit()
        result.changes.append({
            "at": now_iso(), "kind": "found_link", "song_id": case.song_id,
            "text": f"{_song_label(case.song, case.reports[0])}: YouTube link -> {video_id} "
                    "(found by Leina Radio; our search found the same video)",
            "files": sorted({r.file_name for r in case.reports}),
        })

    field_cases = collect_field_correction_cases(session, requests)
    permitted = {}
    open_fields = [c for c in field_cases if c.status == FIELD_OPEN]
    for case in open_fields:
        sender = senders.get(case.latest.file_name)
        if sender is not None and sender not in permitted:
            permitted[sender] = PERMISSION_APPLY_CORRECTIONS in user_permissions(Path(str(Settings.users_dir)), sender)
    # Only links that could be saved are worth asking YouTube about
    to_play = [c for c in open_fields if c.video_id and not c.bad
               and permitted.get(senders.get(c.latest.file_name), False)]
    if to_play:
        check_correction_videos(to_play)
    for case in open_fields:
        sender = senders.get(case.latest.file_name)
        case.auto_note = field_correction_verdict(session, case, sender, permitted.get(sender, False))
        if case.auto_note is not None:
            continue
        old = _current_shown(case)
        apply_field_correction(session, case, ACCEPT, by=AUTO)
        session.commit()
        result.changes.append({
            "at": now_iso(), "kind": "field_correction", "song_id": case.song.id if case.song else None,
            "text": f"{_song_label(case.song, case.latest)}: {case.label} {_shown(old)} -> "
                    f"{_shown(_current_shown(case))} (corrected by {sender})",
            "files": sorted({r.file_name for r in case.reports}),
        })

    song_cases = collect_song_request_cases(session, requests)
    for case in song_cases:
        if case.pending:
            case.auto_note = "adding a song waits for your approval"

    cases = dead_cases + found_cases + field_cases + song_cases
    left_cases = [c for c in cases if c.pending]
    result.to_review = len(left_cases)
    save_case_checks(left_cases)
    result.moved, result.left = finish_request_files(requests, cases, left_to=REVIEW)
    add_auto_report(result.changes, shown=shown)
    for change in result.changes:
        mlog(f"Inbox auto: {change['text']}")
    return result
