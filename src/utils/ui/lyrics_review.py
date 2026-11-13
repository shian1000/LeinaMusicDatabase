"""Songs' lyrics (lyrics.db, utils/database/lyrics_store.py): Fetch database
data -> Fill missing data -> "Lyrics" looks them up with the get_lyrics()
modules (Settings -> Discovery modules -> Lyrics), Song actions -> "Lyrics"
shows or removes them. Sure matches are saved at once; the others are asked
about in one list at the end, then the songs nothing has words for are
offered to be marked instrumental."""

import pyperclip
import questionary

from config.constants import LYRICS_MISS_REJECTED, LYRICS_SOURCE_MANUAL
from utils.common.normalizer import normalize
from utils.common.output import escape, ok, review_header, show
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.fetch_data_settings import load_max_songs_per_fetch
from utils.database.lyrics_store import (
    Lyrics,
    forget_lookups,
    lyrics_db_session,
    mark_instrumental,
    original_lyrics,
    record_miss,
    reject_lyrics,
    save_lyrics,
    song_durations,
    songs_missing_lyrics,
    stored_lyrics,
)
from utils.discoveries.discoveries_manager import discover_lyrics, load_discovery_modules
from utils.discoveries.lyrics_pages import lyrics_from_page
from utils.discoveries.discovery_settings import LYRICS
from utils.ui.prompts import ask_yes_no, choose, tick_items


def _label(song) -> str:
    return f"{song.artist.name} - {song.title}"


def _length(seconds) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}" if seconds else "?"


def _as_lyrics(match) -> Lyrics:
    """What's stored of a match: its synced lines only when they fit our recording."""
    result = match.result
    return Lyrics(plain=result.plain, synced=result.synced if match.synced_fits else None,
                  instrumental=result.instrumental, source=match.module_id, source_ref=result.source_ref)


def _kind(lyrics: Lyrics) -> str:
    return "instrumental" if lyrics.instrumental else "synced" if lyrics.synced else "plain"


def fill_missing_lyrics() -> None:
    session, _ = open_and_set_global_database_sessions()
    modules = load_discovery_modules(LYRICS)
    if not modules:
        show("No lyrics modules are on (Settings -> Discovery modules -> Lyrics).")
        return
    with lyrics_db_session() as lyrics_session:
        pending = songs_missing_lyrics(session, lyrics_session, [module_id for module_id, _, _ in modules])
        if not pending:
            show("Every song has lyrics, is instrumental, or every module was asked about it recently.")
            return
        show(f"{len(pending)} song(s) without lyrics.")
        max_songs = load_max_songs_per_fetch()
        if len(pending) > max_songs:
            show(f"Looking up the first {max_songs} (see Settings to change this).")
            pending = pending[:max_songs]
        _look_up(session, lyrics_session, modules, pending)


def _look_up(session, lyrics_session, modules, pending: list) -> None:
    """Look `pending` ([(song, module ids to ask)]) up, saving as it goes;
    the unsure ones and the songs without words are asked about at the end."""
    durations = song_durations(session, [song for song, _ in pending])
    saved, unsure, wordless, unasked = 0, [], [], 0
    try:
        for position, (song, module_ids) in enumerate(pending, 1):
            show(f"[{position}/{len(pending)}] Looking for lyrics of [yellow]{escape(_label(song))}[/yellow]")
            search = discover_lyrics(song, [m for m in modules if m[0] in module_ids], durations.get(song.id))
            for module_id in search.missed:
                record_miss(lyrics_session, song, module_id)
            unasked += bool(search.failed)
            match = search.match
            if match is not None and not match.sure:
                show(f"    [yellow]to check at the end:[/yellow] {escape(match.result.matched_artist or '')} - "
                     f"{escape(match.result.matched_title or '')}")
                unsure.append((song, match))
            elif match is not None and match.result.instrumental:
                show(f"    [yellow]instrumental, says {escape(match.module_id)} (to check at the end)[/yellow]")
                wordless.append((song, match))
            else:
                lyrics = _as_lyrics(match) if match else original_lyrics(lyrics_session, song)
                if lyrics is not None:
                    save_lyrics(lyrics_session, song.id, lyrics)
                    saved += 1
                    show(f"    [green]found: {_kind(lyrics)}[/green] [dim]({escape(lyrics.source)})[/dim]")
                elif search.failed:
                    show(f"    [red]nothing found[/red] [dim]({', '.join(search.failed)} couldn't be asked)[/dim]")
                else:
                    show("    [red]nothing found[/red]")
                    wordless.append((song, None))
            lyrics_session.commit()
    except KeyboardInterrupt:
        show("\nStopped - what was found so far is kept.")
    show(f"Saved the lyrics of {saved} song(s).")
    if unasked:
        show(f"[dim]{unasked} song(s) weren't asked of every module (no answer, or too many requests) - "
             f"they're asked again next time.[/dim]")
    confirmed = _review_unsure(lyrics_session, unsure)
    if confirmed:
        show(f"Saved {confirmed} more.")
    _offer_instrumental(lyrics_session, wordless)


def _offer_instrumental(lyrics_session, wordless: list) -> None:
    """One checkbox list of the songs no module had words for, and those a
    source says are instrumental (ticked): the ticked ones get language
    "Instrumental" and are never looked up again. An untrue "instrumental"
    left unticked is that module's text turned down."""
    if not wordless:
        return
    review_header(f"{len(wordless)} song(s) have no lyrics anywhere, or a source says they have no words.",
                  "Tick the instrumental ones (space toggles, enter confirms).")

    def label(pair) -> str:
        song, match = pair
        return f"{_label(song)}" + (f"  [instrumental, says {match.module_id}]" if match else "")

    selected = tick_items("Mark as instrumental:", wordless, label, checked=lambda pair: pair[1] is not None)
    if selected is None:
        return
    ticked = {id(pair) for pair in selected}
    for pair in wordless:
        song, match = pair
        if match is not None and id(pair) in ticked:
            save_lyrics(lyrics_session, song.id, _as_lyrics(match))
        elif match is not None:
            record_miss(lyrics_session, song, match.module_id, LYRICS_MISS_REJECTED)
    lyrics_session.commit()
    if selected:
        mark_instrumental([song for song, _ in selected])
        submit_global_database_session()
        show(f"Marked {len(selected)} song(s) instrumental.")


def _review_unsure(lyrics_session, unsure: list) -> int:
    """One checkbox list of the lyrics found for a song only resembling it,
    or with no length of ours to compare: those of an equal title ticked.
    The ticked ones are saved; the rest may be remembered as wrong."""
    if not unsure:
        return 0
    review_header(f"{len(unsure)} song(s) got lyrics that may be another song's or recording's.",
                  "Untick the ones that don't fit (space toggles, enter confirms).")

    def label(pair) -> str:
        song, match = pair
        found = f"{match.result.matched_artist} - {match.result.matched_title}"
        first = _as_lyrics(match).first_line or _kind(_as_lyrics(match))
        return (f"{_label(song)}  <->  {found} ({_length(match.duration_s)} / "
                f"{_length(match.found_duration_s)}) · {first[:60]}")

    selected = tick_items("Save these lyrics:", unsure, label,
                          checked=lambda pair: normalize(pair[1].result.matched_title) == normalize(pair[0].title))
    if selected is None:
        return 0
    for song, match in selected:
        save_lyrics(lyrics_session, song.id, _as_lyrics(match))
    ticked = {id(pair) for pair in selected}
    left = [pair for pair in unsure if id(pair) not in ticked]
    if left and ask_yes_no(f"Remember the {len(left)} unticked one(s) as wrong, so that module isn't asked again?"):
        for song, match in left:
            record_miss(lyrics_session, song, match.module_id, LYRICS_MISS_REJECTED)
    lyrics_session.commit()
    return len(selected)


_LOOK_UP = "Look it up again (every module, as it's named now)"
_PASTE = "Paste the lyrics (from the clipboard)"
_FROM_PAGE = "Read the lyrics from a web page (its address)"
_REMOVE = "Remove (wrong lyrics)"


def lyrics_of_songs(songs: list) -> None:
    """Song actions -> "Lyrics": each song's stored lyrics, and what to do
    with them: look the song up again (what the modules didn't find is
    forgotten), paste a text found elsewhere or read it from its page, or remove wrong ones
    (remembered for their module, so the lookup doesn't bring them back)."""
    session, _ = open_and_set_global_database_sessions()
    with lyrics_db_session() as lyrics_session:
        look_up = []
        for song in songs:
            lyrics = stored_lyrics(lyrics_session, song.id)
            review_header(_label(song))
            if lyrics is None:
                show("No lyrics stored.")
            else:
                show(f"[dim]{_kind(lyrics)}, from {escape(lyrics.source)}[/dim]")
                show(escape(lyrics.plain or "") or "[dim](no words)[/dim]")
            picked = choose("These lyrics:", [_LOOK_UP, _PASTE, _FROM_PAGE, *([_REMOVE] if lyrics else [])],
                            back="Keep")
            if picked == _LOOK_UP:
                forget_lookups(lyrics_session, song.id)
                look_up.append(song)
            elif picked == _PASTE:
                _paste(lyrics_session, song)
            elif picked == _FROM_PAGE:
                _from_page(lyrics_session, song)
            elif picked == _REMOVE:
                reject_lyrics(lyrics_session, song)
                show(f"Removed; {escape(lyrics.source)} won't be asked about this song again (the other modules may).")
            lyrics_session.commit()
        if look_up:
            modules = load_discovery_modules(LYRICS)
            _look_up(session, lyrics_session, modules, [(song, [m[0] for m in modules]) for song in look_up])


def _from_page(lyrics_session, song) -> None:
    """A lyrics page the owner found (a Genius, Tekstowo, ... address, or any
    other site's): its text shown, saved on a yes - the site isn't asked again."""
    url = questionary.text("Address of the page:").ask()
    if not url or not url.strip():
        return
    try:
        text = lyrics_from_page(url.strip())
    except LookupError as exc:
        show(f"[red]The page can't be read ({escape(str(exc))}) - copy the text and paste it instead.[/red]")
        return
    if text is None:
        show("[red]No lyrics told apart on that page[/red] - copy the text and paste it instead.")
        return
    lines = text.splitlines()
    show(escape("\n".join(lines[:6])) + (f"\n[dim]... {len(lines)} lines[/dim]" if len(lines) > 6 else ""))
    if ask_yes_no("Save this text?"):
        save_lyrics(lyrics_session, song.id, Lyrics(plain=text, source=LYRICS_SOURCE_MANUAL, source_ref=url.strip()))
        ok("Saved.")


def _paste(lyrics_session, song) -> None:
    """A text the owner found elsewhere (Musixmatch, Boomplay...), from the
    clipboard when it holds one, edited or typed in a multi-line prompt."""
    try:
        clipboard = pyperclip.paste() or ""
    except pyperclip.PyperclipException:
        clipboard = ""
    show("[dim]Esc then Enter saves; an empty text saves nothing.[/dim]")
    text = questionary.text("Lyrics:", default=clipboard.strip(), multiline=True).ask()
    if not text or not text.strip():
        show("Nothing saved.")
        return
    save_lyrics(lyrics_session, song.id, Lyrics(plain=text.strip(), source=LYRICS_SOURCE_MANUAL))
    ok("Saved.")
