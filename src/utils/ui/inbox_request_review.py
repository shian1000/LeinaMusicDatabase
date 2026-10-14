"""Manage database -> "Resolve new requests": requests in the inbox (so far
only the phone sends them, through the sharing server; other sources may
follow, so the screen never says where a request came from). The logic is utils/database/inbox_requests.py; this
only shows it and asks. Like the other reviews, the slow YouTube checks run
first for every report and the questions come after, in one go: dead links
first, then field corrections (inbox_field_corrections.py). Anything skipped
stays in the inbox for next time."""

import questionary
from rich import print
from rich.markup import escape

from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.inbox_field_corrections import (
    ACCEPT,
    EDIT,
    MERGE_ARTIST,
    REJECT,
    RENAME_ALBUM,
    album_title_twin,
    apply_field_correction,
    artist_twins,
    coerce_field_value,
    collect_field_correction_cases,
    song_album_to_rename,
)
from utils.database.inbox_field_corrections import OPEN as FIELD_OPEN
from utils.database.inbox_field_corrections import UNSUPPORTED as FIELD_UNSUPPORTED
from utils.database.song_artists import count_artist_songs, song_artist_label
from utils.database.inbox_requests import (
    INBOX_DIR,
    KEEP,
    NO_VIDEO,
    OPEN,
    USE_VIDEO,
    apply_dead_link_decision,
    check_dead_link_cases,
    collect_dead_link_cases,
    finish_request_files,
    inbox_available,
    read_new_requests,
    reject_request_file,
)
from utils.youtube.manage_youtube_playlists import NO_VIDEO_SENTINEL, extract_video_id

_PASTE = "Paste another link"
_NO_VIDEO = f'No YouTube video for this song ("{NO_VIDEO_SENTINEL}")'
_KEEP = "Keep the current link"
_SKIP = "Skip for now"
_STOP = "Stop here (the rest stays for next time)"


def _url(video_id: str) -> str:
    return f"https://youtu.be/{video_id}"


def _plays(value: bool | None) -> str:
    if value is None:
        # Rate limiting / YouTube's bot wall: says nothing about the video
        return "[yellow]couldn't check (YouTube blocked it)[/yellow]"
    return "[green]plays here[/green]" if value else "[red]doesn't play here[/red]"


def _not_playing_note(value: bool | None) -> str:
    return " - didn't play here" if value is False else ""


def _show_case(case, position: str) -> None:
    song = case.song
    print(f"\n{position} [yellow]{escape(song.artist.name)} - {escape(song.title)}[/yellow]  (song #{song.id})")
    if case.title_differs:
        shown = ", ".join(sorted({f"{r.artist} - {r.title}" for r in case.reports}))
        print(f"    [red]The request showed it as[/red] {escape(shown)}")
    current = song.youtube_video_id or "none (cleared)"
    dead = "[red]gone (YouTube API)[/red]" if case.dead_still_plays is False else "[dim]reported dead[/dim]"
    print(f"    Dead link:    {_url(case.dead_video_id)}  {dead}"
          + ("" if song.youtube_video_id == case.dead_video_id else f"  [dim](now in database: {current})[/dim]"))
    for r in case.replacements:
        about = " - ".join(escape(x) for x in (r.title, r.channel) if x)
        print(f"    Suggested:    {_url(r.video_id)}  {_plays(case.plays.get(r.video_id))}"
              + (f"  {about}" if about else ""))
    if case.our_video_id:
        same = " [green](same as the suggestion)[/green]" if any(
            r.video_id == case.our_video_id for r in case.replacements) else ""
        print(f"    Newfound:     {_url(case.our_video_id)}  {_plays(case.plays.get(case.our_video_id))}{same}")
    else:
        print("    Newfound:     [dim]our search found nothing[/dim]")
    files = sorted({r.file_name for r in case.reports})
    print(f"    [dim]Reported in {', '.join(files)}[/dim]")


def _choices(case) -> list:
    # Offered even when the check said it doesn't play - the user may know better
    choices = []
    for r in case.replacements:
        also = " - our search found it too" if r.video_id == case.our_video_id else ""
        note = _not_playing_note(case.plays.get(r.video_id))
        choices.append(questionary.Choice(f"Keep the suggested link ({r.video_id}){also}{note}", (USE_VIDEO, r.video_id)))
    if case.our_video_id and not any(r.video_id == case.our_video_id for r in case.replacements):
        note = _not_playing_note(case.plays.get(case.our_video_id))
        choices.append(questionary.Choice(f"Keep the newfound link ({case.our_video_id}){note}", (USE_VIDEO, case.our_video_id)))
    choices += [
        questionary.Choice(_PASTE, _PASTE),
        questionary.Choice(_NO_VIDEO, (NO_VIDEO, None)),
    ]
    if case.song.youtube_video_id == case.dead_video_id:
        choices.append(questionary.Choice(_KEEP, (KEEP, None)))
    choices += [questionary.Choice(_SKIP, _SKIP), questionary.Choice(_STOP, _STOP)]
    return choices


def _ask(case):
    """(decision, video id), _SKIP or _STOP."""
    while True:
        answer = questionary.select("What should the database do?", choices=_choices(case)).ask()
        if answer is None:
            return _STOP
        if answer != _PASTE:
            return answer
        url = (questionary.text("YouTube video link (nothing = back):").ask() or "").strip()
        if not url:
            continue
        video_id = extract_video_id(url)
        if video_id:
            return USE_VIDEO, video_id
        print(f"'{escape(url)}' isn't a YouTube video link.")


# ==================== field corrections ====================

def _value(value) -> str:
    if value is None or value == "":
        return "[dim]empty[/dim]"
    return f'"{escape(str(value))}"' if isinstance(value, str) else escape(str(value))


def _row_label(session, case) -> str:
    row = case.row
    if case.table_name == "artists":
        return f'artist #{row.id} "{escape(row.name)}", {count_artist_songs(session, row.id)} song(s)'
    if case.table_name == "albums":
        return f'album #{row.id} "{escape(row.title)}", {len(row.songs)} song(s)'
    return f"song #{row.id}"


def _show_correction(session, case, position: str) -> None:
    report = case.latest
    song = case.song
    if song is not None:
        print(f"\n{position} [yellow]{escape(song_artist_label(song))} - {escape(song.title)}[/yellow]  (song #{song.id})")
    else:
        print(f"\n{position} [yellow]{escape(report.artist)} - {escape(report.title)}[/yellow]  [red](that song is gone)[/red]")
    if case.title_differs:
        shown = ", ".join(sorted({f"{r.artist} - {r.title}" for r in case.reports}))
        print(f"    [red]The request showed it as[/red] {escape(shown)}")
    print(f"    Field:       {case.label}  [dim]({_row_label(session, case)})[/dim]")
    if case.not_on_song:
        kind = "artist" if case.table_name == "artists" else "album"
        print(f"    [red]This {kind} isn't that song's any more - check it's the one meant.[/red]")
    print(f"    Phone had:   {_value(report.old_value)}")
    changed = "  [yellow](changed since the phone's copy)[/yellow]" if case.changed_since else ""
    print(f"    Now:         {_value(case.current)}{changed}")
    error = f"  [red]can't be stored: {escape(case.error)}[/red]" if case.error else ""
    print(f"    Correction:  {_value(report.new_value)}{error}")
    if case.earlier_values:
        print(f"    [dim]Earlier asked for: {', '.join(_value(v) for v in case.earlier_values)}[/dim]")
    files = sorted({r.file_name for r in case.reports})
    print(f"    [dim]Corrected {escape(report.corrected_at or '?')}, in {', '.join(files)}[/dim]")


def _value_notes(session, case, value) -> list:
    """Warnings for one value the owner might store."""
    notes = []
    for twin in artist_twins(session, case, value):
        notes.append(f'[yellow]Another artist is already called "{escape(twin.name)}" (#{twin.id}, '
                     f"{count_artist_songs(session, twin.id)} song(s)) - the same artist spelt twice?[/yellow]")
    twin = album_title_twin(session, case, value)
    if twin is not None:
        notes.append(f'[yellow]The artist already has an album "{escape(twin.title)}" (#{twin.id}) - '
                     f"saving merges the two.[/yellow]")
    return notes


def _correction_choices(session, case) -> list:
    choices = []
    if not case.error:
        choices.append(questionary.Choice(f"Save the correction ({_plain(case.new_value)})", (ACCEPT, None)))
        album = song_album_to_rename(case)
        if album is not None and case.new_value is not None:
            choices.append(questionary.Choice(
                f'Rename its whole album instead (#{album.id} "{album.title}", {len(album.songs)} songs)',
                (RENAME_ALBUM, None)))
        for twin in artist_twins(session, case, case.new_value):
            choices.append(questionary.Choice(
                f'Merge this artist into "{twin.name}" (#{twin.id}, which stays)', (MERGE_ARTIST, twin)))
    choices += [
        questionary.Choice("Edit the value before saving", EDIT),
        questionary.Choice(f"Keep the current value ({_plain(case.current)})", (REJECT, None)),
        questionary.Choice(_SKIP, _SKIP),
        questionary.Choice(_STOP, _STOP),
    ]
    return choices


def _plain(value) -> str:
    return "empty" if value is None or value == "" else f'"{value}"' if isinstance(value, str) else str(value)


def _ask_correction(session, case):
    """(decision, value), _SKIP or _STOP."""
    for note in _value_notes(session, case, case.new_value):
        print(f"    {note}")
    while True:
        answer = questionary.select("What should the database do?", choices=_correction_choices(session, case)).ask()
        if answer is None:
            return _STOP
        if answer != EDIT:
            return answer
        start = case.new_value if case.new_value is not None else (case.latest.new_value or "")
        typed = questionary.text(f"New {case.label} (empty = no value):", default=str(start)).ask()
        if typed is None:
            continue
        value, error = coerce_field_value(case.table_name, case.column_name, typed)
        if error:
            print(f"[red]{escape(error)}[/red]")
            continue
        notes = _value_notes(session, case, value)
        for note in notes:
            print(f"    {note}")
        if notes and not questionary.confirm(f"Save {_plain(value)} anyway?").ask():
            continue
        return EDIT, value


def _review_field_corrections(session, requests) -> tuple[list, bool]:
    """Returns (cases, stopped)."""
    cases = collect_field_correction_cases(session, requests)
    settled = [c for c in cases if not c.pending]
    unsupported = [c for c in cases if c.status == FIELD_UNSUPPORTED]
    open_cases = [c for c in cases if c.status == FIELD_OPEN]
    if settled:
        print(f"\n{len(settled)} field correction(s) need no answer:")
        for case in settled:
            report = case.latest
            print(f"    {escape(report.artist)} - {escape(report.title)}, {case.label} -> "
                  f"{_value(report.new_value)}: {case.status}")
    if unsupported:
        names = sorted({f"{c.table_name}.{c.column_name}" for c in unsupported})
        print(f"[yellow]{len(unsupported)} correction(s) of {', '.join(names)}, which this version can't correct - "
              f"their files stay in the inbox.[/yellow]")
    if open_cases:
        print(f"\n{len(open_cases)} field correction(s) to look at:")
    for i, case in enumerate(open_cases, 1):
        _show_correction(session, case, f"[{i}/{len(open_cases)}]")
        answer = _ask_correction(session, case)
        if answer == _STOP:
            return cases, True
        if answer == _SKIP:
            continue
        decision, value = answer
        apply_field_correction(session, case, decision, value)
        submit_global_database_session()
    return cases, False


def resolve_new_requests() -> None:
    if not inbox_available():
        print(f"The inbox ({INBOX_DIR}) is on a drive that isn't mounted - is T7 plugged in?")
        return
    requests = read_new_requests()
    if not requests:
        print("No new requests in the inbox.")
        return
    print(f"{len(requests)} request file(s) in the inbox.")

    for request in [r for r in requests if r.error]:
        target = reject_request_file(request)
        print(f"[red]{escape(request.name)}[/red] isn't a readable request ({escape(request.error)}) - moved to {target.parent}")
    requests = [r for r in requests if not r.error]
    for request in requests:
        if not request.supported:
            print(f"[yellow]{escape(request.name)}[/yellow] is request format {request.format}; this version reads "
                  f"format 1 - left in the inbox for a newer version.")
        elif request.unknown_tables:
            print(f"[yellow]{escape(request.name)}[/yellow] also has {', '.join(request.unknown_tables)}, which this "
                  f"version can't handle yet - the file stays in the inbox.")

    session = open_and_set_global_database_sessions()[0]
    cases = collect_dead_link_cases(session, requests)
    settled = [c for c in cases if c.status != OPEN]
    open_cases = [c for c in cases if c.status == OPEN]
    stopped = False
    if settled:
        print(f"\n{len(settled)} dead link report(s) need no answer:")
        for case in settled:
            report = case.reports[0]
            print(f"    {escape(report.artist)} - {escape(report.title)} ({case.dead_video_id}): {case.status}")

    if open_cases:
        print(f"\nLooking for videos for {len(open_cases)} dead link report(s)...")
        stats = check_dead_link_cases(
            open_cases,
            on_case=lambda i, case: print(f"  [{i}/{len(open_cases)}] {escape(case.song.artist.name)} - {escape(case.song.title)}"),
        )
        if not stats["api"]:
            print("[yellow]YouTube Data API not available (no YOUTUBE_API_KEY in .env and no valid login) - "
                  "checked with yt-dlp only.[/yellow]")
        if stats["blocked"]:
            print(f"[yellow]YouTube blocked {stats['blocked']} yt-dlp check(s) (rate limiting / \"confirm you're not a bot\")"
                  f"{' - the API still lists those videos, so they count as playing' if stats['api'] else ''}.[/yellow]")

        for i, case in enumerate(open_cases, 1):
            _show_case(case, f"[{i}/{len(open_cases)}]")
            answer = _ask(case)
            if answer == _STOP:
                stopped = True
                break
            if answer == _SKIP:
                continue
            decision, video_id = answer
            apply_dead_link_decision(case, decision, video_id)
            submit_global_database_session()

    if not stopped:
        field_cases, stopped = _review_field_corrections(session, requests)
        cases = cases + field_cases
    else:
        # Not asked this time, but they still keep their files in new/
        cases = cases + collect_field_correction_cases(session, requests)

    moved, left = finish_request_files(requests, cases)
    if moved:
        print(f"\n{len(moved)} request file(s) done - moved to {INBOX_DIR / 'processed'}.")
    if left:
        print(f"{len(left)} request file(s) stay in the inbox for next time.")
