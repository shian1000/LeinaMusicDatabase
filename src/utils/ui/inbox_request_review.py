"""Manage database -> "Resolve new requests": requests in the inbox (so far
only the phone sends them, through the sharing server; other sources may
follow, so the screen never says where a request came from). The logic is utils/database/inbox_requests.py; this
only shows it and asks. Like the other reviews, the slow YouTube checks run
first for every report and the questions come after, in one go. Anything
skipped stays in the inbox for next time."""

import questionary
from rich import print
from rich.markup import escape

from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
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
                break
            if answer == _SKIP:
                continue
            decision, video_id = answer
            apply_dead_link_decision(case, decision, video_id)
            submit_global_database_session()

    moved, left = finish_request_files(requests, cases)
    if moved:
        print(f"\n{len(moved)} request file(s) done - moved to {INBOX_DIR / 'processed'}.")
    if left:
        print(f"{len(left)} request file(s) stay in the inbox for next time.")
