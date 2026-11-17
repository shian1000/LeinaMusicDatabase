"""
The automatic pass over new requests from the phone: settles on its own what
it safely can, and leaves the rest to the owner's review (Manage database ->
"Resolve new requests", utils/ui/inbox_request_review.py). Run as often as
Settings -> "Automation" says by a systemd user timer
(auto_resolve_requests.py at the repo root) while the app isn't open, and by
the review itself before asking anything. Nothing here asks anything (ADR-0001). Each kind of change below is
a rule of Settings -> "Automation" (constants.AUTOMATION_RULE_LABELS); one
turned off leaves its cases to the owner, with a note saying so.

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

from config.constants import AUTOMATION_RULE_LABELS
from settings import Settings
from utils.common.automation_settings import has_user_rules, rule_on, user_rule_taking
from utils.common.debug import flog, mlog
from utils.common.normalizer import normalize
from utils.common.text_utils import similarity
from utils.database.account_auth import PERMISSION_ADD_SONGS, PERMISSION_APPLY_CORRECTIONS, user_permissions
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
    AUTO,
    LINK_ALREADY_CHANGED,
    OPEN,
    REVIEW,
    USE_VIDEO,
    InboxFileError,
    RequestFile,
    add_auto_report,
    apply_dead_link_decision,
    check_link_cases,
    collect_dead_link_cases,
    finish_request_files,
    now_iso,
    package_entry,
    reject_request_file,
    save_case_checks,
)
from utils.database.inbox_song_requests import (
    collect_song_request_cases,
    mark_song_added,
    song_request_facts,
    videos_saved_text,
)
from utils.database.song_artists import additional_artist_role, song_artist_label


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


def _rule_off(key: str) -> str | None:
    """Why the pass may not settle a case its rule `key` would: None when the rule is on."""
    return None if rule_on(key) else f'the rule "{AUTOMATION_RULE_LABELS[key]}" is off in Settings -> Automation'


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
        if package_entry(case.latest.file_name) is not None:
            return "imported from a request package - its sender name isn't a login"
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


def _taken(blocker: str | None, question: str, built_in_note: str | None, facts: dict | None):
    """(who takes the case, None) - "" for the built-in rule `question`, else
    the name of the owner's rule (Settings -> Automation -> "My rules") whose
    blocks all hold for `facts` - or (None, why nobody does). A `blocker` (a
    change that would be wrong, or another operation) stops both."""
    if blocker:
        # Without rules of the owner's the built-in rule's reason says it as before
        return None, built_in_note if built_in_note and not has_user_rules(question) else blocker
    if built_in_note is None:
        built_in_note = _rule_off(question)
        if built_in_note is None:
            return "", None
    rule = user_rule_taking(question, facts) if facts else None
    return (rule, None) if rule else (None, built_in_note)


def _by(rule: str) -> str:
    return f'; your rule "{rule}"' if rule else ""


def _link_video(case) -> str | None:
    """The video a link case would get: ours when Leina Radio suggested it
    (or nothing), else its suggestion that plays here, else its first."""
    suggested = [r.video_id for r in case.replacements]
    if case.our_video_id and (case.our_video_id in suggested or not suggested):
        return case.our_video_id
    playing = [v for v in suggested if case.plays.get(v) is True]
    return (playing or suggested or [None])[0]


def link_blocker(case) -> str | None:
    """Why no rule may set the link: nothing checked, or no video to put in."""
    if not case.checked_at:
        return "not checked on YouTube"
    if _link_video(case) is None:
        return "Leina Radio suggested only videos found bad before" if case.bad else "no video to put in"
    return None


def link_facts(case, sender: str | None, permitted: bool) -> dict:
    """A link case's facts (constants.AUTOMATION_QUESTIONS), about _link_video()."""
    video = _link_video(case)
    shown = {r.video_id: r for r in case.replacements}.get(video)
    artist = normalize(case.song.artist.name) if case.song is not None else ""
    return {
        "same_video": bool(case.our_video_id) and case.our_video_id in {r.video_id for r in case.replacements},
        "suggested_by_leina_radio": bool(case.replacements),
        "our_search_found": bool(case.our_video_id),
        "plays": case.plays.get(video),
        "video_title_similarity": (round(similarity(shown.title, case.song.title), 3)
                                   if shown and shown.title and case.song is not None else None),
        "channel_is_artist": artist in normalize(shown.channel) if shown and shown.channel and artist else None,
        "logged_in": sender is not None, "sender_may_correct": permitted, "reports": len(case.reports),
    }


def field_correction_blocker(session, case) -> str | None:
    """Why no rule may save the correction: a value that can't be stored, a
    song or row it no longer fits, an artist that can't be told apart, a
    merge (another operation), a video found bad or not checked."""
    if case.error:
        return f"the value can't be stored: {case.error}"
    if case.song is None:
        return "the song it was corrected from is gone"
    if case.not_on_song:
        return "that artist / album isn't the song's any more"
    if case.is_artist:
        matches = artist_choices(session, case, case.new_value)
        if len(matches) > 1:
            return "several artists have that name"
        if matches and additional_artist_role(case.row, matches[0]):
            return "that artist is already credited on the song"
    if artist_twins(session, case, case.new_value):
        return "another artist already has that name (a merge?)"
    if album_title_twin(session, case, case.new_value) is not None:
        return "the artist already has an album of that title (saving merges the two)"
    if case.video_id:
        if case.bad:
            return f"the video was found bad before: {case.bad}"
        if not case.checked_at:
            return "not checked on YouTube"
    return None


def field_correction_facts(session, case, sender: str | None, permitted: bool) -> dict:
    """A field correction's facts (constants.AUTOMATION_QUESTIONS); only for
    a case field_correction_blocker() lets through."""
    old, new = _current_shown(case), case.new_value
    return {
        "field": case.label,
        "similarity": round(similarity(old, new), 3) if isinstance(old, str) and isinstance(new, str) else None,
        "changed_since": bool(case.changed_since), "other_values_asked": bool(case.earlier_values),
        "title_changed": bool(case.title_differs),
        "new_artist": case.is_artist and not artist_choices(session, case, new),
        "old_artist_left_empty": bool(case.is_artist and artist_left_empty(session, case)),
        "looks_like_spelling_fix": not rename_looks_like_other_artist(case, new),
        "whole_album_could_be_renamed": song_album_to_rename(case) is not None,
        "latin_form_to_check": bool(latin_form_to_check(case, new)),
        "video_plays": case.plays.get(case.video_id) if case.video_id else None,
        "logged_in": sender is not None, "sender_may_correct": permitted, "reports": len(case.reports),
    }


def _current_shown(case):
    """The field's value as the report shows it: the song's artist by name, not id."""
    return case.row.artist.name if case.is_artist else case.current


def _applied(session, case, apply) -> bool:
    """Run `apply()` and commit; False when it failed. The failure is rolled
    back, noted on the case (it goes to review/) and logged, so one case
    can't keep the changes already committed out of auto_report.json."""
    status = case.status
    try:
        apply()
        session.commit()
        return True
    except InboxFileError:
        raise  # decisions.json can't be read: nothing more can be recorded
    except Exception as exc:
        session.rollback()
        case.status = status
        case.auto_note = f"couldn't be saved automatically: {exc}"
        flog(f"Inbox auto: {case.key} failed: {exc!r}")
        return False


def auto_resolve(session, requests: list[RequestFile], on_case=None, shown: bool = False) -> AutoResult:
    """The automatic pass over `requests` (read from new/). Commits
    `session` (music.db) after each change and adds the changes to
    auto_report.json (`shown` = the owner is watching, the app ran it) -
    also those made before an error stopped the pass.
    `on_case(i, n, case)` is called before each YouTube search, for progress."""
    result = AutoResult()
    try:
        _resolve(session, requests, on_case, result)
    finally:
        add_auto_report(result.changes, shown=shown)
        for change in result.changes:
            mlog(f"Inbox auto: {change['text']}")
    return result


def _sender_of(case, senders: dict) -> str | None:
    """The newest logged-in sender among the case's reports' files."""
    for report in reversed(case.reports):
        if senders.get(report.file_name):
            return senders[report.file_name]
    return None


def _resolve(session, requests: list[RequestFile], on_case, result: AutoResult) -> None:
    for request in [r for r in requests if r.error]:
        reject_request_file(request)
        result.rejected.append(request)
    requests = [r for r in requests if not r.error]
    senders = {r.name: r.sender for r in requests}
    granted = {}

    def may(sender: str | None, permission: str) -> bool:
        if sender is None or Settings.users_dir is None:
            return False
        if sender not in granted:
            granted[sender] = user_permissions(Path(str(Settings.users_dir)), sender)
        return permission in granted[sender]

    dead_cases = collect_dead_link_cases(session, requests)
    found_cases = collect_found_link_cases(session, requests)
    open_dead = [c for c in dead_cases if c.status == OPEN]
    open_found = [c for c in found_cases if c.status == OPEN]
    to_check = open_dead + open_found
    if to_check:
        # One batch, so the Data API is asked once for everything
        result.check_stats = check_link_cases(
            to_check, on_case=(lambda i, case: on_case(i, len(to_check), case)) if on_case else None)
    for question, cases, kind in (("dead_link_same_video", open_dead, "dead_link"),
                                  ("found_link_same_video", open_found, "found_link")):
        for case in cases:
            if kind == "found_link":
                if case.song.youtube_video_id:
                    # A dead link report for the same song was just settled above
                    case.status = LINK_ALREADY_CHANGED
                    continue
                if settle_if_none_plays(case):
                    continue
            sender = _sender_of(case, senders)
            blocker = link_blocker(case)
            case.auto_facts = None if blocker else link_facts(case, sender, may(sender, PERMISSION_APPLY_CORRECTIONS))
            video_id, note = link_verdict(case)
            rule, case.auto_note = _taken(blocker, question, note if video_id is None else None, case.auto_facts)
            if rule is None:
                continue
            video_id = video_id if rule == "" else _link_video(case)
            if kind == "dead_link":
                applied = _applied(session, case, lambda: apply_dead_link_decision(case, USE_VIDEO, video_id, by=AUTO))
                text = (f"{_song_label(case.song, case.reports[0])}: YouTube link {case.dead_video_id} -> {video_id} "
                        f"(Leina Radio's suggestion; our search found the same video{_by(rule)})" if rule == "" else
                        f"{_song_label(case.song, case.reports[0])}: YouTube link {case.dead_video_id} -> {video_id}"
                        f" ({_by(rule)[2:]})")
            else:
                applied = _applied(session, case, lambda: apply_found_link_decision(case, USE_VIDEO, video_id, by=AUTO))
                text = (f"{_song_label(case.song, case.reports[0])}: YouTube link -> {video_id} "
                        + ("(found by Leina Radio; our search found the same video)" if rule == ""
                           else f"(found by Leina Radio{_by(rule)})"))
            if applied:
                result.changes.append({"at": now_iso(), "kind": kind, "song_id": case.song_id, "text": text,
                                       "files": sorted({r.file_name for r in case.reports})})

    field_cases = collect_field_correction_cases(session, requests)
    open_fields = [c for c in field_cases if c.status == FIELD_OPEN]
    # Only links that could be saved are worth asking YouTube about
    by_rules = has_user_rules("field_correction_permitted")
    to_play = [c for c in open_fields if c.video_id and not c.bad and (by_rules or (
        rule_on("field_correction_permitted")
        and may(senders.get(c.latest.file_name), PERMISSION_APPLY_CORRECTIONS)))]
    if to_play:
        check_correction_videos(to_play)
    for case in open_fields:
        sender = senders.get(case.latest.file_name)
        permitted = may(sender, PERMISSION_APPLY_CORRECTIONS)
        blocker = field_correction_blocker(session, case)
        case.auto_facts = None if blocker else field_correction_facts(session, case, sender, permitted)
        rule, case.auto_note = _taken(blocker, "field_correction_permitted",
                                      field_correction_verdict(session, case, sender, permitted), case.auto_facts)
        if rule is None:
            continue
        old = _current_shown(case)
        if not _applied(session, case, lambda: apply_field_correction(session, case, ACCEPT, by=AUTO)):
            continue
        result.changes.append({
            "at": now_iso(), "kind": "field_correction", "song_id": case.song.id if case.song else None,
            "text": f"{_song_label(case.song, case.latest)}: {case.label} {_shown(old)} -> "
                    f"{_shown(_current_shown(case))} (corrected by {sender or 'someone not logged in'}{_by(rule)})",
            "files": sorted({r.file_name for r in case.reports}),
        })

    song_cases = collect_song_request_cases(session, requests)
    session.commit()  # the videos of requested songs found in the database
    for case in song_cases:
        if any(case.videos_saved):
            result.changes.append({
                "at": now_iso(), "kind": "song_request", "song_id": case.existing.id,
                "text": f"{_song_label(case.existing, case.latest)}: already in the database, "
                        f"{videos_saved_text(case)} (Leina Radio asked for it)",
                "files": sorted({r.file_name for r in case.reports}),
            })
    _add_requested_songs(session, [c for c in song_cases if c.pending], senders, may, result)

    cases = dead_cases + found_cases + field_cases + song_cases
    left_cases = [c for c in cases if c.pending]
    result.to_review = len(left_cases)
    save_case_checks(left_cases)
    result.moved, result.left = finish_request_files(requests, cases, left_to=REVIEW)


def _add_requested_songs(session, cases: list, senders: dict, may, result: AutoResult) -> None:
    """The songs one of the owner's rules for "song_request" takes, added
    through the import with nobody to ask (its questions about a similar
    song or artist answered by the owner's rules for them, else no - a new
    song, a new artist); the rest wait for the owner. Facts cost a
    MusicBrainz lookup, so they're gathered only when such a rule exists."""
    if not cases:
        return
    if not has_user_rules("song_request"):
        for case in cases:
            case.auto_note = ("adding a song waits for your approval (or a rule of yours in "
                              "Settings -> Automation -> My rules)")
        return
    from utils.discoveries.import_engine import LocalSongMatcher, run_import_batch
    from utils.ui.prompts import unattended

    matcher = LocalSongMatcher(session)
    taken = []
    for case in cases:
        artist, title = case.suggested or (case.latest.artist, case.latest.title)
        if not (artist and title):
            case.auto_note = "it has no artist or no title to be added as"
            continue
        sender = _sender_of(case, senders)
        case.auto_facts = song_request_facts(case, artist, title, senders, matcher,
                                             may(sender, PERMISSION_ADD_SONGS))
        rule = user_rule_taking("song_request", case.auto_facts)
        if rule is None:
            case.auto_note = "no rule of yours (Settings -> Automation -> My rules) takes it"
            continue
        taken.append((case, artist, title, rule))
    if not taken:
        return
    # The import works in the menu's sessions: this one must hold no
    # transaction, or SQLite keeps its commits waiting
    session.commit()
    try:
        with unattended() as answered:
            run_import_batch([case.metadata(artist, title) for case, artist, title, _ in taken])
    except Exception as exc:
        flog(f"Inbox auto: adding requested songs failed: {exc!r}")
        for case, *_ in taken:
            case.auto_note = f"adding it automatically failed: {exc}"
        return
    for case, artist, title, rule in taken:
        mark_song_added(case, artist, title, by=AUTO)
        result.changes.append({"at": now_iso(), "kind": "song_request", "song_id": None,
                               "text": f"{artist} - {title}: added (Leina Radio asked for it{_by(rule)})",
                               "files": sorted({r.file_name for r in case.reports})})
    result.changes += [{"at": now_iso(), "kind": "song_request", "song_id": None, "text": f"{step or 'Import'}: {item}",
                        "files": []} for step, item in answered]
