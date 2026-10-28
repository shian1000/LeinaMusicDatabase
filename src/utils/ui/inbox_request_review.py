"""Manage database -> "Resolve new requests": requests in the inbox (so far
only the phone sends them, through the sharing server; other sources may
follow, so the screen never says where a request came from). The logic is utils/database/inbox_requests.py; this
only shows it and asks. Files nobody has looked at yet (new/) first go
through the same automatic pass the background timer runs
(utils/database/inbox_auto.py); then everything it left to the owner
(review/) is asked about, with the pass's note on why. Like the other
reviews, the slow YouTube checks run first for every report (reusing the
pass's check while it's fresh) and the questions come after, in one go: dead
links first, then links found for songs without one (inbox_found_links.py),
then field corrections (inbox_field_corrections.py), then songs Leina Radio
asked to have added (inbox_song_requests.py; the approved ones go through the
import together at the end of that part). Anything
skipped stays in review/ for next time. The screen also lists what the
automatic pass changed."""

import questionary
from utils.ui.prompts import ask_yes_no
from utils.ui.song_colors import as_rich_markup, song_tokens
from rich import print
from rich.markup import escape

from config.constants import INBOX_CHECK_MAX_AGE_HOURS, SONG_KIND_FULL_ALBUM
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.inbox_auto import auto_resolve
from utils.database.inbox_field_corrections import (
    ACCEPT,
    EDIT,
    MERGE_ARTIST,
    MOVE_TO_ARTIST,
    NEW_ARTIST,
    REJECT,
    RENAME_ALBUM,
    album_title_twin,
    apply_field_correction,
    artist_choices,
    artist_twins,
    check_correction_videos,
    coerce_field_value,
    collect_field_correction_cases,
    credit_target_song,
    latin_form_to_check,
    rename_looks_like_other_artist,
    replaced_video,
    song_album_to_rename,
)
from utils.database.inbox_found_links import REJECTED, apply_found_link_decision, collect_found_link_cases
from utils.database.inbox_song_requests import (
    collect_song_request_cases,
    is_generic_channel_artist,
    mark_song_added,
    reject_song_request,
)
from utils.database.inbox_field_corrections import OPEN as FIELD_OPEN
from utils.database.inbox_field_corrections import UNSUPPORTED as FIELD_UNSUPPORTED
from utils.database.latin_names import LATIN_COLUMNS, name_of
from utils.database.artist_reassignment import is_unused
from utils.database.datatables import Artist
from utils.database.song_artists import additional_artist_role, count_artist_songs, song_artist_label
from utils.ui.artist_change import ask_old_artist_role, offer_removing_emptied
from utils.common.romanize import suggest_latin
from utils.database.inbox_requests import (
    INBOX_DIR,
    KEEP,
    NO_VIDEO,
    OPEN,
    USE_VIDEO,
    apply_dead_link_decision,
    auto_report,
    check_link_cases,
    collect_dead_link_cases,
    describe_files,
    finish_request_files,
    inbox_available,
    inbox_unavailable_message,
    load_case_checks,
    mark_auto_report_shown,
    new_request_paths,
    read_new_requests,
    read_review_requests,
    reject_request_file,
    review_request_paths,
)
from config.constants import NO_VIDEO_SENTINEL
from utils.youtube.manage_youtube_playlists import extract_video_id

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


def _show_bad(case) -> None:
    for r in case.bad_replacements:
        print(f"    [dim]Found bad:    {_url(r.video_id)}  {escape(case.bad[r.video_id])}[/dim]")


def _show_ours(case) -> None:
    if case.our_video_id:
        same = " [green](same as Leina Radio's)[/green]" if any(
            r.video_id == case.our_video_id for r in case.replacements) else ""
        print(f"    Our search:   {_url(case.our_video_id)}  {_plays(case.plays.get(case.our_video_id))}{same}")
    else:
        print("    Our search:   [dim]found nothing[/dim]")


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
    _show_bad(case)
    _show_ours(case)
    files = escape(describe_files({r.file_name for r in case.reports}))
    print(f"    [dim]Reported in {files}[/dim]")
    _show_auto_note(case)


def _show_auto_note(case) -> None:
    if case.auto_note:
        print(f"    [cyan]Left for you by the automatic check: {escape(case.auto_note)}[/cyan]")


def _video_choices(case, label: str) -> list:
    """The phone's videos, ours, those found bad before, paste, "N/A".
    "Use ..." takes a new video; "Keep ..." (_KEEP) only ever means leaving
    what the database has.
    Offered even when the check said it doesn't play - the user may know better."""
    choices = []
    for r in case.replacements:
        also = " - our search found it too" if r.video_id == case.our_video_id else ""
        note = _not_playing_note(case.plays.get(r.video_id))
        choices.append(questionary.Choice(f"Use {label} ({r.video_id}){also}{note}", (USE_VIDEO, r.video_id)))
    if case.our_video_id and not any(r.video_id == case.our_video_id for r in case.replacements):
        note = _not_playing_note(case.plays.get(case.our_video_id))
        choices.append(questionary.Choice(f"Use the link our search found ({case.our_video_id}){note}", (USE_VIDEO, case.our_video_id)))
    for r in case.bad_replacements:
        choices.append(questionary.Choice(f"Use {label} ({r.video_id}) - found bad before: {case.bad[r.video_id]}",
                                          (USE_VIDEO, r.video_id)))
    return choices + [
        questionary.Choice(_PASTE, _PASTE),
        questionary.Choice(_NO_VIDEO, (NO_VIDEO, None)),
    ]


def _choices(case) -> list:
    choices = _video_choices(case, "the suggested link")
    if case.song.youtube_video_id == case.dead_video_id:
        choices.append(questionary.Choice(_KEEP, (KEEP, None)))
    choices += [questionary.Choice(_SKIP, _SKIP), questionary.Choice(_STOP, _STOP)]
    return choices


def _ask(case, choices: list):
    """(decision, video id), _SKIP or _STOP."""
    while True:
        answer = questionary.select("What should the database do?", choices=choices).ask()
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


def _check(cases: list, what: str) -> None:
    """The automatic pass's check, while fresh; YouTube only for the rest."""
    load_case_checks(cases, INBOX_CHECK_MAX_AGE_HOURS)
    unchecked = [c for c in cases if not c.checked_at]
    if not unchecked:
        return
    print(f"\nLooking for videos for {len(unchecked)} {what}...")
    stats = check_link_cases(
        unchecked,
        on_case=lambda i, case: print(f"  [{i}/{len(unchecked)}] {escape(case.song.artist.name)} - {escape(case.song.title)}"),
    )
    _print_check_stats(stats)


def _print_check_stats(stats: dict) -> None:
    if not stats["api"]:
        print("[yellow]YouTube Data API not available (no YOUTUBE_API_KEY in .env and no valid login) - "
              "checked with yt-dlp only.[/yellow]")
    if stats.get("blocked"):
        print(f"[yellow]YouTube blocked {stats['blocked']} yt-dlp check(s) (rate limiting / \"confirm you're not a bot\")"
              f"{' - the API still lists those videos, so they count as playing' if stats['api'] else ''}.[/yellow]")


# ==================== found links ====================

def _show_found(case, position: str) -> None:
    song = case.song
    print(f"\n{position} [yellow]{escape(song_artist_label(song))} - {escape(song.title)}[/yellow]  (song #{song.id})")
    if case.title_differs:
        shown = ", ".join(sorted({f"{r.artist} - {r.title}" for r in case.reports}))
        print(f"    [red]The request showed it as[/red] {escape(shown)}")
    print("    Now:          [dim]no YouTube link[/dim]")
    for i, r in enumerate(case.replacements):
        about = " - ".join(escape(x) for x in (r.title, r.channel) if x)
        latest = "  [dim](its latest pick)[/dim]" if i == 0 and len(case.replacements) > 1 else ""
        print(f"    Leina Radio:  {_url(r.video_id)}  {_plays(case.plays.get(r.video_id))}"
              + (f"  {about}" if about else "") + latest)
    _show_bad(case)
    _show_ours(case)
    files = escape(describe_files({r.file_name for r in case.reports}))
    print(f"    [dim]Found in {files}[/dim]")
    _show_auto_note(case)


def _found_choices(case) -> list:
    return _video_choices(case, "Leina Radio's link") + [
        questionary.Choice("Wrong video - leave the song without a link (not offered again)", (REJECTED, None)),
        questionary.Choice(_SKIP, _SKIP),
        questionary.Choice(_STOP, _STOP),
    ]


def _review_found_links(session, requests) -> tuple[list, bool]:
    """Returns (cases, stopped)."""
    cases = collect_found_link_cases(session, requests)
    settled = [c for c in cases if c.status != OPEN]
    open_cases = [c for c in cases if c.status == OPEN]
    if settled:
        print(f"\n{len(settled)} link(s) found by Leina Radio need no answer:")
        for case in settled:
            report = case.reports[0]
            print(f"    {escape(report.artist)} - {escape(report.title)} "
                  f"({', '.join(r.video_id for r in case.all_replacements())}): {case.status}")
    if not open_cases:
        return cases, False
    _check(open_cases, "song(s) Leina Radio found a link for")
    print(f"\n{len(open_cases)} link(s) Leina Radio found for songs without one:")
    for i, case in enumerate(open_cases, 1):
        _show_found(case, f"[{i}/{len(open_cases)}]")
        answer = _ask(case, _found_choices(case))
        if answer == _STOP:
            return cases, True
        if answer == _SKIP:
            continue
        decision, video_id = answer
        apply_found_link_decision(case, decision, video_id)
        submit_global_database_session()
    return cases, False


# ==================== field corrections ====================

def _value(value) -> str:
    if value is None or value == "":
        return "[dim]empty[/dim]"
    return f'"{escape(str(value))}"' if isinstance(value, str) else escape(str(value))


def _artist_ref(session, artist_id) -> str:
    """The song's artist as shown: songs.artist_id by name."""
    artist = session.get(Artist, artist_id) if isinstance(artist_id, int) else None
    if artist is None:
        return f"#{artist_id} [dim](no longer in the database)[/dim]" if artist_id is not None else _value(None)
    return f'"{escape(artist.name)}" (#{artist.id}, {count_artist_songs(session, artist.id)} song(s))'


def _field_value(case, value) -> str:
    """A value as shown: a YouTube column's video as a link."""
    if case.is_video and value and value != NO_VIDEO_SENTINEL:
        return _url(str(value))
    if case.is_video and value == NO_VIDEO_SENTINEL:
        return f'"{NO_VIDEO_SENTINEL}" [dim](not on YouTube)[/dim]'
    return _value(value)


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
    print(f"    Field:           {case.label}  [dim]({_row_label(session, case)})[/dim]")
    if case.row is not None and case.column_name == LATIN_COLUMNS[type(case.row)][1]:
        name = name_of(case.row)
        suggestion = suggest_latin(name, getattr(case.row, "language", None))
        hint = f"  [dim](suggested: {escape(suggestion)})[/dim]" if suggestion else ""
        print(f"    Written as:      {escape(name)}{hint}")
    if case.not_on_song:
        kind = "artist" if case.table_name == "artists" else "album"
        print(f"    [red]This {kind} isn't that song's any more - check it's the one meant.[/red]")
    had, now = ((_artist_ref(session, report.old_value), _artist_ref(session, case.current)) if case.is_artist
                else (_field_value(case, report.old_value), _field_value(case, case.current)))
    print(f"    Leina Radio had: {had}")
    changed = "  [yellow](changed since Leina Radio's copy)[/yellow]" if case.changed_since else ""
    print(f"    Now:             {now}{changed}")
    error = f"  [red]can't be stored: {escape(case.error)}[/red]" if case.error else ""
    shown = case.new_value if case.is_video and not case.error else report.new_value
    plays = f"  {_plays(case.plays.get(case.video_id))}" if case.video_id and case.checked_at else ""
    print(f"    Correction:      {_field_value(case, shown)}{plays}{error}")
    if case.bad:
        print(f"    [yellow]That video was found bad before: {escape(case.bad)}[/yellow]")
    if case.earlier_values:
        print(f"    [dim]Earlier asked for: {', '.join(_field_value(case, v) for v in case.earlier_values)}[/dim]")
    if case.is_artist:
        print("    [dim]Only this song moves to that artist - its current artist is not renamed.[/dim]")
    elif rename_looks_like_other_artist(case, case.new_value) and credit_target_song(case) is not None:
        print("    [yellow]The new name doesn't look like a spelling fix - maybe only this song is by another "
              "artist.[/yellow]")
    if case.is_video:
        print("    [dim]Leina Radio already uses this link; keeping the current value leaves it on that link "
              "until the database's value changes.[/dim]")
    files = escape(describe_files({r.file_name for r in case.reports}))
    print(f"    [dim]Corrected {escape(report.corrected_at or '?')}, in {files}[/dim]")
    _show_auto_note(case)


def _value_notes(session, case, value) -> list:
    """Warnings for one value the owner might store."""
    notes = []
    for twin in artist_twins(session, case, value):
        notes.append(f'[yellow]Another artist is already called "{escape(twin.name)}" (#{twin.id}, '
                     f"{count_artist_songs(session, twin.id)} song(s)) - the same artist spelt twice?[/yellow]")
    latin = latin_form_to_check(case, value)
    if latin:
        notes.append(f'[yellow]Its Latin form "{escape(latin)}" stays as it is - change it in the editor '
                     f"if it no longer fits.[/yellow]")
    twin = album_title_twin(session, case, value)
    if twin is not None:
        notes.append(f'[yellow]The artist already has an album "{escape(twin.title)}" (#{twin.id}) - '
                     f"saving merges the two.[/yellow]")
    return notes


def _artist_target_choices(session, case, name, prefix: str = "") -> list:
    """The answers crediting the song to an artist called `name`."""
    choices = [questionary.Choice(
        f'{prefix}Credit the song to "{twin.name}" (#{twin.id}, {count_artist_songs(session, twin.id)} song(s))',
        (MOVE_TO_ARTIST, twin)) for twin in artist_choices(session, case, name)]
    also = " (another artist of that name)" if choices else ""
    choices.append(questionary.Choice(f'{prefix}Credit it to a new artist "{name}"{also}', (NEW_ARTIST, name)))
    return choices


def _correction_choices(session, case) -> list:
    if case.is_artist:
        return [
            *([] if case.error else _artist_target_choices(session, case, case.new_value)),
            questionary.Choice("Type another artist", EDIT),
            questionary.Choice(f'Keep "{case.row.artist.name}"', (REJECT, None)),
            questionary.Choice(_SKIP, _SKIP),
            questionary.Choice(_STOP, _STOP),
        ]
    choices = []
    # An artists.name correction may really mean "this song is another artist's"
    credit = [] if case.error or credit_target_song(case) is None else \
        _artist_target_choices(session, case, case.new_value, prefix="Only this song: ")
    other_artist = rename_looks_like_other_artist(case, case.new_value)
    if other_artist:
        choices += credit
    if not case.error:
        choices.append(questionary.Choice(
            f'Rename the artist for all its songs ({_plain(case.new_value)})' if credit
            else f"Save the correction ({_plain(case.new_value)})", (ACCEPT, None)))
        album = song_album_to_rename(case)
        if album is not None and case.new_value is not None:
            choices.append(questionary.Choice(
                f'Rename its whole album instead (#{album.id} "{album.title}", {len(album.songs)} songs)',
                (RENAME_ALBUM, None)))
        for twin in artist_twins(session, case, case.new_value):
            choices.append(questionary.Choice(
                f'Merge this artist into "{twin.name}" (#{twin.id}, which stays)', (MERGE_ARTIST, twin)))
    if not other_artist:
        choices += credit
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
        if case.is_artist:
            typed = questionary.text("Artist's name:", default=case.new_value or "").ask()
            if not typed or not typed.strip():
                continue
            picked = questionary.select("Which artist?", choices=[
                *_artist_target_choices(session, case, typed.strip()),
                questionary.Choice("Back", None)]).ask()
            if picked is None:
                continue
            return picked
        start = case.new_value if case.new_value is not None else (case.latest.new_value or "")
        hint = (f'link or id, "{NO_VIDEO_SENTINEL}" = not on YouTube, ' if case.column_name == "youtube_video_id"
                else "link or id, " if case.is_video
                else f'"{SONG_KIND_FULL_ALBUM}" = a whole album, ' if case.column_name == "kind" else "")
        typed = questionary.text(f"New {case.label} ({hint}empty = no value):", default=str(start)).ask()
        if typed is None:
            continue
        value, error = coerce_field_value(case.table_name, case.column_name, typed)
        if error:
            print(f"[red]{escape(error)}[/red]")
            continue
        notes = _value_notes(session, case, value)
        for note in notes:
            print(f"    {note}")
        if notes and not ask_yes_no(f"Save {_plain(value)} anyway?"):
            continue
        return EDIT, value


def _credit_to_artist(session, case, decision, value) -> None:
    """A songs.artist_id answer, with the questions around it: what the old
    artist becomes when the new one is already on the song, and what to do
    with an old artist left without songs (as in the song editor)."""
    song = credit_target_song(case)
    old = song.artist
    keep_old_as = None
    if decision == MOVE_TO_ARTIST and additional_artist_role(song, value):
        keep_old_as = ask_old_artist_role(song, value)
        if keep_old_as is False:
            return
    apply_field_correction(session, case, decision, value, keep_old_as=keep_old_as)
    print(f"Now: {escape(song_artist_label(song))} - {escape(song.title)}")
    if is_unused(session, old):
        offer_removing_emptied(session, [old], song.artist)


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
        load_case_checks(open_cases, INBOX_CHECK_MAX_AGE_HOURS)
        unchecked = [c for c in open_cases if c.video_id and not c.checked_at]
        if unchecked:
            print(f"\nChecking {len(unchecked)} YouTube link(s) set in Leina Radio...")
            _print_check_stats(check_correction_videos(unchecked))
        print(f"\n{len(open_cases)} field correction(s) to look at:")
    for i, case in enumerate(open_cases, 1):
        _show_correction(session, case, f"[{i}/{len(open_cases)}]")
        answer = _ask_correction(session, case)
        if answer == _STOP:
            return cases, True
        if answer == _SKIP:
            continue
        decision, value = answer
        if decision in (MOVE_TO_ARTIST, NEW_ARTIST):
            _credit_to_artist(session, case, decision, value)
            submit_global_database_session()
            continue
        old_video_wrong = False
        if decision in (ACCEPT, EDIT):
            old = replaced_video(case, case.new_value if decision == ACCEPT else value)
            if old:
                old_video_wrong = bool(ask_yes_no(f"Also remember the replaced video ({old}) as wrong for this song? "
                    "(It won't be used for it again, here or in Leina Radio.)"))
        apply_field_correction(session, case, decision, value, old_video_wrong=old_video_wrong)
        submit_global_database_session()
    return cases, False


# ==================== songs to add ====================

_ADD = "add"
_ADD_SUGGESTED = "add suggested"
_CORRECT = "correct"
_REJECT_SONG = "reject"


def _song_label(artist, title) -> str:
    return f"{artist or '(no artist)'} - {title}"


def _colored_song_label(artist, title) -> str:
    """_song_label() as rich markup, the artist and the title in song_colors'
    colours - a name with " - " in it otherwise hides where one ends."""
    return as_rich_markup(song_tokens(artist or "(no artist)", title))


def _show_song_request(case, position: str) -> None:
    report = case.latest
    print(f"\n{position} {_colored_song_label(report.artist, report.title)}")
    other = sorted({(r.artist, r.title) for r in case.reports} - {(report.artist, report.title)},
                   key=lambda pair: _song_label(*pair))
    if other:
        print(f"    Also asked for as {', '.join(_colored_song_label(*pair) for pair in other)}")
    print(f"    From:         {escape(', '.join(case.sources))}")
    album, year = case.metadata()["album"], case.metadata()["year"]
    if album or year:
        print(f"    Album, year:  {escape(str(album or '-'))}, {year or '-'}")
    for i, suggestion in enumerate(case.suggestions):
        label = "Looks like:" if i == 0 else "or:"
        print(f"    {label:<14}{_colored_song_label(*suggestion)}  [dim]({escape(case.suggested_from or '')})[/dim]")
    for video_id in case.youtube_video_ids:
        print(f"    YouTube:      {_url(video_id)}")
    for track_id in case.spotify_track_ids:
        print(f"    Spotify:      https://open.spotify.com/track/{track_id}")
    files = escape(describe_files({r.file_name for r in case.reports}))
    print(f"    [dim]Asked {escape(report.requested_at or '?')}, in {files}[/dim]")
    _show_auto_note(case)


def _ask_song_request(case):
    """(_ADD, artist, title), _REJECT_SONG, _SKIP or _STOP."""
    report = case.latest
    suggested = case.suggested
    choices = [
        *[questionary.Choice([("", "Add it as "), *song_tokens(*suggestion)], (_ADD_SUGGESTED, suggestion))
          for suggestion in case.suggestions],
        # A generic Topic channel ("Release") is never the artist
        *([questionary.Choice([("", "Add it as asked (" if suggested else "Add it to the database ("),
                               *song_tokens(report.artist, report.title), ("", ")")], _ADD)]
          if report.artist and not is_generic_channel_artist(report.artist) else []),
        questionary.Choice("Correct the artist / title, then add it", _CORRECT),
        questionary.Choice("Reject - Leina Radio won't ask for it again", _REJECT_SONG),
        questionary.Choice(_SKIP, _SKIP),
        questionary.Choice(_STOP, _STOP),
    ]
    while True:
        answer = questionary.select("What should the database do?", choices=choices).ask()
        if answer is None:
            return _STOP
        if isinstance(answer, tuple) and answer[0] == _ADD_SUGGESTED:
            return _ADD, *answer[1]
        if answer == _ADD:
            return _ADD, report.artist, report.title
        if answer != _CORRECT:
            return answer
        default_artist, default_title = suggested or (report.artist, report.title)
        artist = (questionary.text("Artist:", default=default_artist or "").ask() or "").strip()
        title = (questionary.text("Title:", default=default_title or "").ask() or "").strip()
        if artist and title:
            return _ADD, artist, title
        print("[red]Both the artist and the title are needed.[/red]")


def _add_requested_songs(approved: list) -> None:
    """The approved songs through the import, in one batch (its questions
    about similar artists / songs come at the end, as for any import)."""
    if not approved:
        return
    from utils.discoveries.import_engine import run_import_batch

    print(f"\nAdding {len(approved)} song(s) Leina Radio asked for...")
    run_import_batch([case.metadata(artist, title) for case, artist, title in approved])
    submit_global_database_session()
    for case, artist, title in approved:
        mark_song_added(case, artist, title)


def _review_song_requests(session, requests) -> tuple[list, bool]:
    """Returns (cases, stopped)."""
    cases = collect_song_request_cases(session, requests)
    settled = [c for c in cases if not c.pending]
    open_cases = [c for c in cases if c.pending]
    if settled:
        print(f"\n{len(settled)} song(s) Leina Radio asked for need no answer:")
        for case in settled:
            report = case.latest
            also = (f" (#{case.existing.id} {escape(song_artist_label(case.existing))} - {escape(case.existing.title)})"
                    if case.existing is not None else "")
            print(f"    {escape(_song_label(report.artist, report.title))}: {case.status}{also}")
    if open_cases:
        print(f"\n{len(open_cases)} song(s) Leina Radio played that aren't in the database:")
    approved = []
    stopped = False
    for i, case in enumerate(open_cases, 1):
        _show_song_request(case, f"[{i}/{len(open_cases)}]")
        answer = _ask_song_request(case)
        if answer == _STOP:
            stopped = True
            break
        if answer == _SKIP:
            continue
        if answer == _REJECT_SONG:
            reject_song_request(session, case)
            submit_global_database_session()
            continue
        _, artist, title = answer
        approved.append((case, artist, title))
    # What was approved before a stop is still added
    _add_requested_songs(approved)
    return cases, stopped


def show_auto_changes(entries: list[dict]) -> None:
    for entry in entries:
        when = (entry.get("at") or "?")[:16].replace("T", " ")
        print(f"  [dim]{escape(when)}[/dim]  {escape(entry.get('text', '?'))}")


def _auto_pass(session) -> None:
    """The automatic pass over new/, shown as it goes."""
    requests = read_new_requests()
    print(f"Checking {len(requests)} new request file(s) automatically first...")
    result = auto_resolve(
        session, requests, shown=True,
        on_case=lambda i, n, case: print(f"  [{i}/{n}] {escape(case.song.artist.name)} - {escape(case.song.title)}"))
    for request in result.rejected:
        print(f"[red]{escape(request.name)}[/red] isn't a readable request ({escape(request.error)}) - "
              f"moved to {INBOX_DIR / 'rejected'}")
    if result.check_stats:
        _print_check_stats(result.check_stats)
    if result.changes:
        print(f"\n[green]Changed automatically ({len(result.changes)}):[/green]")
        show_auto_changes(result.changes)
    print(f"{len(result.moved)} file(s) done, {len(result.left)} left for you to review.")


def resolve_new_requests() -> None:
    if not inbox_available():
        print(f"The inbox isn't available: {inbox_unavailable_message()}")
        return
    new_count, review_count = len(new_request_paths()), len(review_request_paths())
    history = auto_report()
    print(f"New request files (not checked yet): {new_count}")
    print(f"Waiting for your review (checked automatically): {review_count}")
    review_label = "Review the requests"
    history_label = f"Show what the automatic check changed ({len(history)})"
    choices = ([review_label] if new_count or review_count else []) + ([history_label] if history else [])
    if not choices:
        print("No new requests in the inbox.")
        return
    answer = questionary.select("What now?", choices=choices + ["Back"]).ask()
    if answer == history_label:
        show_auto_changes(history)
        mark_auto_report_shown()
        return
    if answer != review_label:
        return

    session = open_and_set_global_database_sessions()[0]
    if new_count:
        _auto_pass(session)
    requests = read_review_requests()
    if not requests:
        return
    print(f"\n{len(requests)} request file(s) to review.")

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
        _check(open_cases, "dead link report(s)")
        for i, case in enumerate(open_cases, 1):
            _show_case(case, f"[{i}/{len(open_cases)}]")
            answer = _ask(case, _choices(case))
            if answer == _STOP:
                stopped = True
                break
            if answer == _SKIP:
                continue
            decision, video_id = answer
            apply_dead_link_decision(case, decision, video_id)
            submit_global_database_session()

    # Not asked after a stop, but they still keep their files in review/
    if not stopped:
        found_cases, stopped = _review_found_links(session, requests)
    else:
        found_cases = collect_found_link_cases(session, requests)
    if not stopped:
        field_cases, stopped = _review_field_corrections(session, requests)
    else:
        field_cases = collect_field_correction_cases(session, requests)
    if not stopped:
        song_cases, stopped = _review_song_requests(session, requests)
    else:
        song_cases = collect_song_request_cases(session, requests)
    cases = cases + found_cases + field_cases + song_cases

    moved, left = finish_request_files(requests, cases)
    if moved:
        print(f"\n{len(moved)} request file(s) done - moved to {INBOX_DIR / 'processed'}.")
    if left:
        print(f"{len(left)} request file(s) stay in the inbox for next time.")
