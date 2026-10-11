"""Manage database -> "Resolve albums with several artists": album titles
that more than one main artist uses are never given an album row
automatically (utils/database/albums.py) - the user settles each one here.
Resumable: an unsettled title is just songs with album_id NULL, so stopping
halfway loses nothing. Also offered at the end of an import that created
such a case (run_import_batch())."""

from collections import defaultdict

import questionary
from rich import print
from rich.markup import escape

from config.constants import ALBUM_TYPE_COMPILATION, ALBUM_TYPE_SOUNDTRACK
from utils.database.albums import AlbumTitleGroup, assign_songs_to_album, find_unresolved_album_groups
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session

_COMPILATION = "One album: a compilation (various artists)"
_SOUNDTRACK = "One album: a soundtrack (various artists)"
_PER_ARTIST = "Separate albums, one per artist"
_BY_HAND = "Split into albums by hand"
_SKIP = "Skip for now"
_STOP = "Stop here (the rest stays for next time)"

_VARIOUS = {
    "Various artists (compilation)": ALBUM_TYPE_COMPILATION,
    "Various artists (soundtrack)": ALBUM_TYPE_SOUNDTRACK,
}


def _song_label(song) -> str:
    year = song.year or "?"
    where = f"  [album #{song.album_id}]" if song.album_id else ""
    return f"{song.artist.name} - {song.title} ({year}){where}"


def _show_group(group: AlbumTitleGroup, position: str) -> None:
    artists = len(group.artist_ids)
    print(f"\n{position} Album [yellow]{escape(group.title)}[/yellow] - "
          f"{len(group.songs)} song(s) by {artists} artists:")
    for song in group.songs:
        print(f"    {escape(_song_label(song))}")


def _split_by_hand(session, group: AlbumTitleGroup) -> None:
    remaining = list(group.songs)
    while remaining:
        picked = questionary.checkbox(
            "Tick the songs on one album (space toggles, enter confirms; none = stop):",
            choices=[questionary.Choice(title=_song_label(s), value=i) for i, s in enumerate(remaining)],
        ).ask()
        if not picked:
            return
        songs = [remaining[i] for i in picked]
        artists = {s.artist.name: s.artist for s in songs}
        choice = questionary.select(
            "Whose album is it?", choices=sorted(artists) + list(_VARIOUS)
        ).ask()
        if choice is None:
            return
        if choice in _VARIOUS:
            album = assign_songs_to_album(session, songs, group.title, None, _VARIOUS[choice])
        else:
            album = assign_songs_to_album(session, songs, group.title, artists[choice])
        submit_global_database_session()
        print(f"{len(songs)} song(s) -> album #{album.id}")
        remaining = [s for s in remaining if s not in songs]


def _resolve_group(session, group: AlbumTitleGroup, choice: str) -> None:
    if choice in (_COMPILATION, _SOUNDTRACK):
        album_type = ALBUM_TYPE_COMPILATION if choice == _COMPILATION else ALBUM_TYPE_SOUNDTRACK
        album = assign_songs_to_album(session, group.songs, group.title, None, album_type)
        print(f"All {len(group.songs)} song(s) -> album #{album.id}")
    elif choice == _PER_ARTIST:
        by_artist = defaultdict(list)
        for song in group.songs:
            by_artist[song.artist].append(song)
        for artist, songs in by_artist.items():
            album = assign_songs_to_album(session, songs, group.title, artist)
            print(f"{escape(artist.name)}: {len(songs)} song(s) -> album #{album.id}")
    elif choice == _BY_HAND:
        _split_by_hand(session, group)


def review_multi_artist_albums(songs: list | None = None) -> int:
    """Go through the album titles several main artists share - all of them,
    or only the titles of `songs` (an import passes the songs it added).
    Returns how many titles were settled."""
    music_session, _ = open_and_set_global_database_sessions()
    groups = find_unresolved_album_groups(music_session, songs)
    if not groups:
        if songs is None:
            print("No album titles shared by several artists left to resolve.")
        return 0

    print("\n" + "=" * 70)
    print(f"REVIEW: {len(groups)} album title(s) are used by several artists")
    print("=" * 70)
    resolved = 0
    for i, group in enumerate(groups, 1):
        _show_group(group, f"[{i}/{len(groups)}]")
        choice = questionary.select(
            "What is this album?",
            choices=[_COMPILATION, _SOUNDTRACK, _PER_ARTIST, _BY_HAND, _SKIP, _STOP],
        ).ask()
        if choice is None or choice == _STOP:
            break
        if choice == _SKIP:
            continue
        _resolve_group(music_session, group, choice)
        submit_global_database_session()
        resolved += 1

    left = len(find_unresolved_album_groups(music_session, songs))
    print(f"\nSettled {resolved} album title(s); {left} left for later.")
    return resolved


def offer_album_resolution(songs: list) -> int:
    """End of an import: ask about the albums of `songs` that landed on a
    title other artists already use, if any did."""
    pending = [s for s in songs if (s.album or "").strip() and s.album_id is None]
    if not pending:
        return 0
    music_session, _ = open_and_set_global_database_sessions()
    if not find_unresolved_album_groups(music_session, pending):
        return 0
    print(f"\n{len(pending)} imported song(s) have an album title other artists also use.")
    if not questionary.confirm("Resolve those albums now?", default=True).ask():
        print('Left for later: Manage database -> "Resolve albums with several artists".')
        return 0
    return review_multi_artist_albums(pending)
