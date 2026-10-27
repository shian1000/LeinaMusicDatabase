"""Manage database -> "Find full albums": one ticked list of songs that may
really be whole albums (utils/database/full_album_candidates.py), then one
question about taking "(Full Album)" out of the marked titles, then one about
remembering the unticked ones."""

import questionary
from utils.ui.prompts import ask_yes_no
from rich import print
from rich.markup import escape

from config.constants import FULL_ALBUM_MIN_DURATION_S, SONG_KIND_FULL_ALBUM
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.files_index import long_song_files
from utils.database.full_album_candidates import FullAlbumCandidate, find_full_album_candidates, unmarked_video_ids
from utils.database.song_artists import song_artist_label
from utils.database.song_kinds import add_to_full_album_ignore_list, set_song_kind
from utils.database.video_durations import video_durations


def _describe(candidate: FullAlbumCandidate) -> str:
    song = candidate.song
    album = f"  album: {song.album}" if song.album else ""
    return f"{song_artist_label(song)} - {song.title}  ({', '.join(candidate.reasons)}){album}"


def review_full_albums() -> int:
    """Returns how many songs were marked as full albums."""
    music_session, _ = open_and_set_global_database_sessions()
    file_durations = long_song_files(FULL_ALBUM_MIN_DURATION_S)
    print("Asking YouTube how long the songs' videos are...")
    video_durations_by_id = video_durations(music_session, unmarked_video_ids(music_session))
    if video_durations_by_id is None:
        print("[yellow]YouTube's API isn't set up (YOUTUBE_API_KEY) or didn't answer - "
              "only titles and library files are checked.[/yellow]")
    candidates = find_full_album_candidates(music_session, file_durations, video_durations_by_id)
    if not candidates:
        print("No songs look like full albums.")
        return 0

    print("\n" + "=" * 70)
    print(f"REVIEW: {len(candidates)} song(s) may be whole albums")
    print(f"Pre-selected: the title says so. Recordings over {FULL_ALBUM_MIN_DURATION_S // 60} min "
          "may also be mixes or live sets - tick only real albums.")
    print("=" * 70)
    choices = [
        questionary.Choice(title=_describe(c), value=i, checked=c.title_says_so)
        for i, c in enumerate(candidates)
    ]
    selected = questionary.checkbox(
        "Select the full albums (space toggles, enter confirms):", choices=choices
    ).ask()
    if selected is None:
        print("Aborted, nothing marked.")
        return 0
    marked = [candidates[i] for i in selected]

    renames = [c for c in marked if c.cleaned_title != c.song.title]
    if renames:
        print("\nTitles with the full-album words taken out:")
        for c in renames:
            print(f"  [red]{escape(c.song.title)}[/red] -> [green]{escape(c.cleaned_title)}[/green]")
        if ask_yes_no(f"Change these {len(renames)} title(s)?"):
            for c in renames:
                c.song.title = c.cleaned_title

    filed = set_song_kind([c.song for c in marked], SONG_KIND_FULL_ALBUM)
    submit_global_database_session()
    for song in filed:
        print(f'  [cyan]{escape(song_artist_label(song))}[/cyan] - "{escape(song.title)}" had no album - '
              f'filed under "{escape(song.album)}".')

    skipped = [c.song.id for i, c in enumerate(candidates) if i not in set(selected)]
    if skipped and ask_yes_no(f"Remember the {len(skipped)} unselected song(s) as not albums, so they aren't proposed again?"):
        add_to_full_album_ignore_list(skipped)
        print(f"Remembered {len(skipped)} song(s).")

    print(f"Marked {len(marked)} song(s) as full albums.")
    return len(marked)
