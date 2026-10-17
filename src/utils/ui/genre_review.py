"""Genres of songs (songs.genre, utils/database/genres.py): Fetch database
data -> Fill missing data -> "Genres", the song editor's "genre" and Song
actions -> "Set genre". Every value is stored in the database's one spelling
per genre (clean_genres()); nothing the owner typed is overwritten - the
lookup only fills songs with no genre."""

import questionary
from utils.ui.prompts import ask_yes_no
from rich import print
from rich.markup import escape

from config.constants import GENRE_LEVEL_ARTIST
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.fetch_data_settings import load_max_songs_per_fetch
from utils.database.genres import (
    genre_key,
    known_genre_spellings,
    set_song_genre,
    songs_missing_genre,
    split_genres,
)
from utils.discoveries.genre_lookup import GenreLookup, load_misses, save_misses
from utils.common.musicbrainz_client import MBStats


def _label(song) -> str:
    return f"{song.artist.name} - {song.title}"


def _remember_spellings(known: dict, value) -> None:
    for name in split_genres(value):
        known.setdefault(genre_key(name), name)


def fill_missing_genres() -> None:
    session, _ = open_and_set_global_database_sessions()
    songs = songs_missing_genre(session)
    if not songs:
        print("Every song has a genre.")
        return
    misses = load_misses()
    skipped = [s for s in songs if s.id in misses]
    songs = [s for s in songs if s.id not in misses]
    print(f"{len(songs) + len(skipped)} song(s) without a genre.")
    if skipped:
        print(f"[dim]{len(skipped)} of them had nothing on MusicBrainz before and are skipped "
              f"(delete data/genre_lookup_misses.json to look them up again).[/dim]")
    max_songs = load_max_songs_per_fetch()
    if len(songs) > max_songs:
        print(f"Looking up the first {max_songs} (see Settings to change this).")
        songs = songs[:max_songs]

    known = known_genre_spellings(session)
    lookup = GenreLookup(known)
    MBStats.reset()
    saved, artist_level, not_found = 0, [], []
    try:
        for position, song in enumerate(songs, 1):
            print(f"[{position}/{len(songs)}] Checking genre for [yellow]{escape(_label(song))}[/yellow]")
            result = lookup.lookup(song)
            if result is None:
                print("    nothing found")
                not_found.append(song)
                misses.add(song.id)
            elif result.level == GENRE_LEVEL_ARTIST:
                print(f"    [dim]artist's genre (to review at the end):[/dim] {escape(result.genres)}")
                artist_level.append((song, result))
            else:
                set_song_genre(session, song, result.genres, known)
                _remember_spellings(known, song.genre)
                submit_global_database_session()
                saved += 1
                print(f"    {escape(song.genre)} [dim]({result.level})[/dim]")
    except KeyboardInterrupt:
        print("\nStopped - what was found so far is kept.")
    finally:
        save_misses(misses)
    print(f"Saved the genre of {saved} song(s).")
    print(MBStats.format_summary())

    saved_artist = _review_artist_genres(session, artist_level, known)
    typed = _type_missing(session, not_found, known) if not_found else 0
    if saved_artist or typed:
        print(f"Saved {saved_artist + typed} more.")


def _review_artist_genres(session, artist_level: list, known: dict) -> int:
    """One checkbox list of the songs that only got their artist's genres,
    all ticked; the ticked ones are saved."""
    if not artist_level:
        return 0
    print("\n" + "=" * 70)
    print(f"{len(artist_level)} song(s) only got their artist's genres from MusicBrainz.")
    print("Untick the ones that don't fit the song (space toggles, enter confirms).")
    print("=" * 70)
    choices = [questionary.Choice(title=f"{_label(song)}: {result.genres}", value=i, checked=True)
               for i, (song, result) in enumerate(artist_level)]
    selected = questionary.checkbox("Save these genres:", choices=choices).ask()
    if not selected:
        return 0
    for i in selected:
        song, result = artist_level[i]
        set_song_genre(session, song, result.genres, known)
        _remember_spellings(known, song.genre)
    submit_global_database_session()
    return len(selected)


def _type_missing(session, songs: list, known: dict) -> int:
    if not ask_yes_no(f"MusicBrainz had nothing for {len(songs)} song(s). Type their genres one by one?"):
        return 0
    print("Comma-separated, the main one first. Leave it empty to skip that song, Ctrl+C to stop.")
    typed = 0
    for position, song in enumerate(songs, 1):
        print(f"\n[{position}/{len(songs)}] [yellow]{escape(_label(song))}[/yellow]  "
              f"[dim]({escape(song.album or '—')})[/dim]")
        value = questionary.text("Genre:").ask()
        if value is None:
            break
        if not value.strip():
            continue
        set_song_genre(session, song, value, known)
        _remember_spellings(known, song.genre)
        submit_global_database_session()
        print(f"    saved as {escape(song.genre or '—')}")
        typed += 1
    return typed


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
        print("Aborted")
        return
    # One song may respell a genre only it has
    known = known_genre_spellings(session, exclude=songs[0] if len(songs) == 1 else None)
    for song in songs:
        old = set_song_genre(session, song, value, known)
        if len(songs) == 1:
            print(f"Genre of {escape(_label(song))}: {escape(old or '---')} --> {escape(song.genre or '---')}")
    submit_global_database_session()
    if len(songs) > 1:
        print(f"Set the genre of {len(songs)} song(s) to {escape(songs[0].genre or '---')}.")
