"""Manage database -> "Resolve albums with several artists": album titles
that more than one main artist uses are never given an album row
automatically (utils/database/albums.py) - the user settles each one here.
Resumable: an unsettled title is just songs with album_id NULL, so stopping
halfway loses nothing. Also offered at the end of an import that created
such a case (run_import_batch())."""

from collections import defaultdict

import questionary
from utils.ui.prompts import ask_yes_no
from rich import print
from rich.markup import escape

from config.constants import ALBUM_TYPE_COMPILATION, ALBUM_TYPE_SOUNDTRACK
from utils.database.albums import (
    AlbumTitleGroup,
    assign_songs_to_album,
    clear_album_text,
    find_unresolved_album_groups,
    link_unassigned_songs,
)
from utils.database.artist_identity import find_same_artist_pairs, find_swapped_songs, fix_swapped_song
from utils.database.database_management import merge_artists_in_db
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session

_MERGE = "Merge artists that are one artist spelt differently"
_SWAP = "Fix songs with artist and title swapped"
_COMPILATION = "One album: a compilation (various artists)"
_SOUNDTRACK = "One album: a soundtrack (various artists)"
_PER_ARTIST = "Separate albums, one per artist"
_BY_HAND = "Split into albums by hand"
_DELETE = "Delete album entry for both artists"
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
    elif choice == _DELETE:
        clear_album_text(group.songs)
        print(f"Album name cleared from {len(group.songs)} song(s)")


def _merge_same_artists(session, pairs, declined: set) -> bool:
    """Ask about each pair of rows that look like one artist; merge the ones
    confirmed. Returns whether anything was merged."""
    merged = False
    gone = set()
    # Ids read up front: a merged-away artist's row can't be read after the commit
    for a_id, b_id, a, b in [(a.id, b.id, a, b) for a, b in pairs]:
        if a_id in gone or b_id in gone:
            continue
        print(f"[yellow]{escape(a.name)}[/yellow] ({len(a.songs)} song(s)) and "
              f"[yellow]{escape(b.name)}[/yellow] ({len(b.songs)} song(s)) look like one artist.")
        keep_a, keep_b, different = f"Keep \"{a.name}\"", f"Keep \"{b.name}\"", "Different artists"
        choice = questionary.select(
            "Merge them? The other name is kept as a synonym.",
            choices=[keep_a, keep_b, different],
        ).ask()
        if choice is None or choice == different:
            declined.add(frozenset((a.id, b.id)))
            continue
        keep, drop = (a, b) if choice == keep_a else (b, a)
        drop_id = drop.id  # the row is gone after the commit
        merge_artists_in_db(drop, keep)
        submit_global_database_session()
        gone.add(drop_id)
        merged = True
    return merged


def _fix_swaps(session, swaps, declined_swaps: set) -> bool:
    """Let the user tick which flagged songs really have artist and title
    swapped, and fix those; the unticked ones aren't asked about again.
    Returns whether anything changed."""
    labels = {f"{song.artist.name} - {song.title}  ->  {artist.name} - {song.artist.name}": (song, artist)
              for song, artist in swaps}
    picked = questionary.checkbox(
        "Tick the songs whose artist and title are swapped (space toggles, enter confirms):",
        choices=list(labels),
    ).ask()
    if picked is None:
        return False
    declined_swaps.update(song.id for label, (song, _) in labels.items() if label not in picked)
    if not picked:
        return False
    for label in picked:
        song, artist = labels[label]
        old = fix_swapped_song(session, song, artist)
        note = f" (artist \"{old.name}\" had no other song, removed)" if old else ""
        print(f"Now: {escape(artist.name)} - {escape(song.title)}{escape(note)}")
    submit_global_database_session()
    return True


def review_multi_artist_albums(songs: list | None = None) -> int:
    """Go through the album titles several main artists share - all of them,
    or only the titles of `songs` (an import passes the songs it added).
    Returns how many titles were settled.

    A title often only looks shared because of the artist data: one artist
    under two spellings ("björk" / "Вjörk") or a song with artist and title
    swapped ("Renegade - Styx"). Those are offered first (artist_identity.py);
    fixing them can settle this title and others, so the list is rebuilt
    after each fix."""
    music_session, _ = open_and_set_global_database_sessions()
    # A title left NULL while two artists shared it stays NULL after the other
    # artist's songs leave it (artist merge, album edit) - link those first.
    linked = link_unassigned_songs(music_session, report_linked=True)
    submit_global_database_session()
    if linked and songs is None:
        print(f"Linked {linked} song(s) whose album title only one artist uses now.")
    groups = find_unresolved_album_groups(music_session, songs)
    if not groups:
        if songs is None:
            print("No album titles shared by several artists left to resolve.")
        return 0

    print("\n" + "=" * 70)
    print(f"REVIEW: {len(groups)} album title(s) are used by several artists")
    print("=" * 70)
    resolved = 0
    done = set()       # titles settled or skipped
    declined = set()   # artist pairs the user said are different artists
    declined_swaps = set()  # song ids the user said aren't swapped
    while True:
        groups = [g for g in find_unresolved_album_groups(music_session, songs) if g.key not in done]
        if not groups:
            break
        group = groups[0]
        _show_group(group, f"[{len(done) + 1}/{len(done) + len(groups)}]")
        pairs = [(a, b) for a, b in find_same_artist_pairs(group.songs)
                 if frozenset((a.id, b.id)) not in declined]
        swaps = [(song, artist) for song, artist in find_swapped_songs(group.songs)
                 if song.id not in declined_swaps]
        for a, b in pairs:
            print(f"  [cyan]Same artist?[/cyan] {escape(a.name)} / {escape(b.name)}")
        for song, artist in swaps:
            print(f"  [cyan]Swapped?[/cyan] {escape(song.artist.name)} - {escape(song.title)}")
        choice = questionary.select(
            "What is this album?",
            choices=([_MERGE] if pairs else []) + ([_SWAP] if swaps else [])
                    + [_COMPILATION, _SOUNDTRACK, _PER_ARTIST, _BY_HAND, _DELETE, _SKIP, _STOP],
        ).ask()
        if choice is None or choice == _STOP:
            break
        if choice in (_MERGE, _SWAP):
            changed = (_merge_same_artists(music_session, pairs, declined) if choice == _MERGE
                       else _fix_swaps(music_session, swaps, declined_swaps))
            if changed:
                # merge_artists_in_db() works in raw SQL: reload, then link the
                # titles that now have a single artist (this one, maybe others)
                music_session.expire_all()
                relinked = link_unassigned_songs(music_session, report_linked=True)
                submit_global_database_session()
                if relinked:
                    print(f"Linked {relinked} song(s) whose album title only one artist uses now.")
            continue  # the same title again if it's still shared, with what's left
        done.add(group.key)
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
    if not ask_yes_no("Resolve those albums now?"):
        print('Left for later: Manage database -> "Resolve albums with several artists".')
        return 0
    return review_multi_artist_albums(pending)
