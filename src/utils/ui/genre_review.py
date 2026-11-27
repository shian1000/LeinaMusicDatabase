"""Genres of songs (songs.genre, utils/database/genres.py): Fetch database
data -> Fill missing data -> "Genres", the song editor's "genre" and Song
actions -> "Set genre". Every value is stored in the database's one spelling
per genre (clean_genres()); nothing the owner typed is overwritten - the
lookup only fills songs with no genre.

The lookup asks the get_genre() modules (Settings -> Discovery modules ->
Genres) through discoveries_manager.discover_genre(): a sure source's
(MusicBrainz's) song or album genres are saved at once; an artist's genres
and the never_sure modules' (Wikipedia, iTunes, Google) are asked in one list
at the end - only the artist's ticked. A module that had nothing for a song
isn't asked about it again (GENRE_LOOKUP_MISSES_FILE, per module), so a new
module still asks every song once."""

import questionary
from utils.common.automation_settings import recent_misses, remember_misses
from utils.common.json_file import read_json, write_json
from utils.ui.prompts import ask_yes_no, stop_requested, tick_items, type_each, unattended_now

from config.constants import (GENRE_ARTIST_MIN_SONGS, GENRE_ARTIST_SONGS_MISSES, GENRE_ARTIST_TICK_SHARE,
                              GENRE_LEVEL_ARTIST, GENRE_LOOKUP_MISSES_FILE, GENRE_MODULE_MAX_FAILURES)
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.fetch_data_settings import load_max_songs_per_fetch
from utils.database.genres import (
    artist_genre_proposal,
    artist_genres,
    genre_key,
    known_genre_spellings,
    set_song_genre,
    songs_missing_genre,
    split_genres,
)
from utils.discoveries.discoveries_manager import discover_genre, load_discovery_modules
from utils.discoveries.discovery_settings import GENRES
from utils.common.musicbrainz_client import MBStats
from utils.common.output import escape, review_header, show

# The file's first form, a list of song ids, was MusicBrainz's alone
_OLD_MISSES_MODULE = "music_brainz_fetcher"


def load_misses() -> dict:
    """{module id: song ids} the module had no genre for, not asked again."""
    old = read_json(GENRE_LOOKUP_MISSES_FILE, [])
    data = {_OLD_MISSES_MODULE: old} if old else read_json(GENRE_LOOKUP_MISSES_FILE, {}, "Genre lookup misses")
    return {module: {i for i in ids if isinstance(i, int)} for module, ids in data.items() if isinstance(ids, list)}


def save_misses(misses: dict) -> None:
    write_json(GENRE_LOOKUP_MISSES_FILE, {module: sorted(ids) for module, ids in misses.items() if ids})


def _label(song) -> str:
    return f"{song.artist.name} - {song.title}"


def _remember_spellings(known: dict, value) -> None:
    for name in split_genres(value):
        known.setdefault(genre_key(name), name)


def fill_missing_genres() -> None:
    session, _ = open_and_set_global_database_sessions()
    modules = load_discovery_modules(GENRES)
    if not modules:
        show("No genre modules are on (Settings -> Discovery modules -> Genres).")
        return
    songs = songs_missing_genre(session)
    if not songs:
        show("Every song has a genre.")
        return
    misses = load_misses()
    missing = list(songs)

    failures = {}  # module id -> failures in a row; GENRE_MODULE_MAX_FAILURES drops it for this run

    def modules_for(song):  # those that haven't had nothing for it before
        return [module for module in modules if song.id not in misses.get(module[0], ())
                and failures.get(module[0], 0) < GENRE_MODULE_MAX_FAILURES]

    # The automation also leaves out the songs it left without a genre lately
    left_lately = recent_misses("genres") if unattended_now() else set()
    skipped = [s for s in songs if not modules_for(s)]
    songs = [s for s in songs if modules_for(s) and s.id not in left_lately]
    show(f"{len(songs) + len(skipped)} song(s) without a genre.")
    if skipped:
        show(f"[dim]{len(skipped)} of them had nothing in any genre module before and are skipped "
             f"(delete data/genre_lookup_misses.json to look them up again).[/dim]")
    max_songs = load_max_songs_per_fetch()
    if len(songs) > max_songs:
        show(f"Looking up the first {max_songs} (see Settings to change this).")
        songs = songs[:max_songs]

    known = known_genre_spellings(session)
    names = {module_id: name for module_id, name, _ in modules}
    MBStats.reset()
    saved, unsure, not_found = 0, [], []
    try:
        for position, song in enumerate(songs, 1):
            if stop_requested():  # the automation: its time is up, or the app waits
                show("Stopped - what was found so far is kept.")
                break
            show(f"[{position}/{len(songs)}] Checking genre for [yellow]{escape(_label(song))}[/yellow]")
            search = discover_genre(song, modules_for(song))
            for module_id in search.missed:
                misses.setdefault(module_id, set()).add(song.id)
            for module_id, _, _ in modules:
                if module_id in search.failed:
                    failures[module_id] = failures.get(module_id, 0) + 1
                    if failures[module_id] == GENRE_MODULE_MAX_FAILURES:
                        show(f"    [dim]{escape(names[module_id])} failed {GENRE_MODULE_MAX_FAILURES} times in a "
                             f"row - not asked again in this run (debug.log says why).[/dim]")
                elif module_id in search.missed or (search.match and search.match.module_id == module_id):
                    failures[module_id] = 0
            match = search.match
            if match is None:
                show("    nothing found")
                not_found.append(song)
            elif not match.sure:
                show(f"    [dim]{escape(_source(match, names))} (to review at the end):[/dim] {escape(match.genres)}")
                unsure.append((song, match))
            else:
                set_song_genre(session, song, match.genres, known)
                _remember_spellings(known, song.genre)
                submit_global_database_session()
                saved += 1
                show(f"    {escape(song.genre)} [dim]({escape(_source(match, names))})[/dim]")
    except KeyboardInterrupt:
        show("\nStopped - what was found so far is kept.")
    finally:
        save_misses(misses)
    show(f"Saved the genre of {saved} song(s).")
    show(MBStats.format_summary())

    saved_unsure = _review_unsure_genres(session, unsure, known, names, misses)
    if unattended_now():
        remember_misses("genres", [song.id for song, _ in unsure if not song.genre])
    # Every enabled module has had nothing for these
    known_to_none = [song for song in missing if not song.genre
                     and all(song.id in misses.get(module_id, ()) for module_id, _, _ in modules)]
    saved_artist = _review_artist_song_genres(session, known_to_none, known, misses)
    not_found = [song for song in not_found if not song.genre]
    typed = _type_missing(session, not_found, known) if not_found else 0
    if saved_unsure or saved_artist or typed:
        show(f"Saved {saved_unsure + saved_artist + typed} more.")


def _source(match, names: dict) -> str:
    """"MusicBrainz fetcher (Curl), the artist's" - which module, and whose genres."""
    return f"{names.get(match.module_id, match.module_id)}, the {match.result.level}'s"


def _review_unsure_genres(session, unsure: list, known: dict, names: dict, misses: dict) -> int:
    """One checkbox list of the genres no sure source gave: an artist's
    (ticked), and a never_sure module's (not); the ticked ones are saved. One
    the owner unticks counts as that module's miss, so the next run asks the
    next module (not unattended: nobody looked at it then)."""
    if not unsure:
        return 0
    review_header(f"{len(unsure)} song(s) whose genres no sure source gave.",
                  "Pre-selected: MusicBrainz's genres of the song's artist (it had none for the song or its album). "
                  "Not: what Wikipedia, iTunes or Google say - their genres are broad or unsourced.",
                  "Untick the ones that don't fit the song (space toggles, enter confirms).")
    selected = tick_items("Save these genres:", unsure,
                          lambda pair: f"{_label(pair[0])}: {pair[1].genres}  ({_source(pair[1], names)})",
                          checked=lambda pair: not pair[1].result.never_sure, rule="genres_artist",
                          facts=lambda pair: {"genres": pair[1].genres,
                                              "genre_count": len(split_genres(pair[1].genres)),
                                              "module": pair[1].module_id, "level": pair[1].result.level,
                                              "artist_level": pair[1].result.level == GENRE_LEVEL_ARTIST})
    if selected is None:
        return 0
    if not unattended_now():
        for song, match in unsure:
            if not any(picked is song for picked, _ in selected):
                misses.setdefault(match.module_id, set()).add(song.id)
        save_misses(misses)
    for song, match in selected:
        set_song_genre(session, song, match.genres, known)
        _remember_spellings(known, song.genre)
    submit_global_database_session()
    return len(selected)


def _review_artist_song_genres(session, songs: list, known: dict, misses: dict) -> int:
    """For the songs no module knows: one checkbox list of the genres their
    artist's other songs in music.db share (artist_genre_proposal()), ticked
    when the artist has GENRE_ARTIST_MIN_SONGS songs with a genre and
    GENRE_ARTIST_TICK_SHARE of them the main one. An unticked one isn't
    offered again (not unattended: nobody looked at it then)."""
    turned_down = misses.get(GENRE_ARTIST_SONGS_MISSES, set())
    counts = artist_genres(session)
    proposals = [(song, proposal) for song in songs if song.id not in turned_down
                 and (proposal := artist_genre_proposal(counts.get(song.artist_id)))]
    if not proposals:
        return 0
    review_header(f"{len(proposals)} song(s) no genre module knows, offered their artist's genres from the database",
                  "The genres at least half of the artist's other songs with a genre have, the main one first.",
                  f"Pre-selected: the artist has {GENRE_ARTIST_MIN_SONGS}+ songs with a genre and "
                  f"{GENRE_ARTIST_TICK_SHARE:.0%}+ of them have the main one.")

    def ticked(pair):
        _, share, artist_songs = pair[1]
        return artist_songs >= GENRE_ARTIST_MIN_SONGS and share >= GENRE_ARTIST_TICK_SHARE

    selected = tick_items("Save these genres:", proposals,
                          lambda pair: f"{_label(pair[0])}: {pair[1][0]}  (the artist: "
                                       f"{round(pair[1][1] * pair[1][2])}/{pair[1][2]} songs)",
                          checked=ticked, rule="genres_artist_songs",
                          facts=lambda pair: {"genres": pair[1][0], "artist_songs": pair[1][2],
                                              "share": round(pair[1][1], 3)})
    if selected is None:
        return 0
    if not unattended_now():
        picked = {song.id for song, _ in selected}
        misses.setdefault(GENRE_ARTIST_SONGS_MISSES, set()).update(
            song.id for song, _ in proposals if song.id not in picked)
        save_misses(misses)
    for song, (genres, _, _) in selected:
        set_song_genre(session, song, genres, known)
        _remember_spellings(known, song.genre)
    submit_global_database_session()
    return len(selected)


def _type_missing(session, songs: list, known: dict) -> int:
    if not ask_yes_no(f"No genre module had anything for {len(songs)} song(s). Type their genres one by one?"):
        return 0
    show("Comma-separated, the main one first. Leave it empty to skip that song, Ctrl+C to stop.")

    def save(song, value):
        set_song_genre(session, song, value, known)
        _remember_spellings(known, song.genre)
        submit_global_database_session()
        show(f"    saved as {escape(song.genre or '—')}")

    return type_each(songs, lambda song: f"[yellow]{escape(_label(song))}[/yellow]  "
                                         f"[dim]({escape(song.album or '—')})[/dim]", "Genre:", save)


def set_genre_of_songs(songs: list) -> None:
    """Song actions -> "Set genre", and the song editor for one song:
    the same genre(s) for every song given; empty clears it."""
    if not songs:
        return
    session, _ = open_and_set_global_database_sessions()
    current = {s.genre for s in songs}
    start = (next(iter(current)) or "") if len(current) == 1 else ""
    label = _label(songs[0]) if len(songs) == 1 else f"{len(songs)} songs"
    value = questionary.text(f"Genre of {label} (comma-separated, main one first, empty = none):",
                             default=start).ask()
    if value is None:
        show("Aborted")
        return
    # One song may respell a genre only it has
    known = known_genre_spellings(session, exclude=songs[0] if len(songs) == 1 else None)
    for song in songs:
        old = set_song_genre(session, song, value, known)
        if len(songs) == 1:
            show(f"Genre of {escape(_label(song))}: {escape(old or '---')} --> {escape(song.genre or '---')}")
    submit_global_database_session()
    if len(songs) > 1:
        show(f"Set the genre of {len(songs)} song(s) to {escape(songs[0].genre or '---')}.")
