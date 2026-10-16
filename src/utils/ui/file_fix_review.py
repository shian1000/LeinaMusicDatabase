"""Manage database -> "Fix names and tags for mp3 files that are in the
database": pick a folder, see which files would be renamed / re-tagged after
their song (utils/discoveries/file_fixes.py), and confirm once for all."""

from pathlib import Path

import questionary

from settings import settings
from utils.database.database_sessions import open_and_set_global_database_sessions
from utils.database.song_artists import song_artist_label
from utils.discoveries.file_fixes import apply_file_fix, plan_file_fixes
from utils.discoveries.file_matching_settings import (
    load_auto_accept_artist_similarity,
    load_closest_auto_accept_score,
)
from utils.ui.menu_utils import open_file_browser_terminal
from utils.ui.song_colors import colored_song

RED = "\033[91m"
DIM = "\033[90m"
RESET = "\033[0m"


def _in_library(folder: Path) -> bool:
    try:
        folder.resolve().relative_to(Path(settings.local_library_dir_str).resolve())
        return True
    except ValueError:
        return False


def fix_file_names_and_tags() -> None:
    folder = open_file_browser_terminal(settings.export_dir)
    if not folder:
        return
    folder = Path(str(folder))
    if _in_library(folder):
        print("This folder is in the music library: a renamed file is a new file to the library index")
        print("(files.db) at its next scan, with a new id for Leina Radio.")
        if not questionary.confirm("Go on?", default=False).ask():
            return

    music_session, _ = open_and_set_global_database_sessions()
    print(f"Matching the mp3 files in {folder} with the database...")
    plan = plan_file_fixes(music_session, folder, load_auto_accept_artist_similarity(),
                           load_closest_auto_accept_score())

    if plan.fixes:
        print(f"\n{len(plan.fixes)} file(s) whose name or tags don't match their song in the database:")
        for fix in plan.fixes:
            where = fix.file.parent.relative_to(folder) if fix.file.parent != folder else None
            print(f"  {fix.file.name}" + (f"  {DIM}({where}){RESET}" if where else ""))
            changes = [f"-> {fix.new_name}"] if fix.new_name else []
            if fix.tags_differ:
                changes.append("tags")
            print(f"    {colored_song(song_artist_label(fix.song), fix.song.title)}   {', '.join(changes)}")
    print(f"\n{plan.up_to_date} file(s) already named and tagged like their song; "
          f"{len(plan.not_in_database)} without a song found automatically "
          f'("Create playlist from folder" matches those by hand).')
    if not plan.fixes:
        print("Nothing to fix.")
        return
    if not questionary.confirm("Fix the names and tags of these mp3 files?", default=True).ask():
        print("Nothing changed.")
        return

    failed = []
    for fix in plan.fixes:
        try:
            apply_file_fix(fix)
        except Exception as exc:
            failed.append((fix.file, exc))
    print(f"Fixed {len(plan.fixes) - len(failed)} file(s).")
    for file, exc in failed:
        print(f"{RED}  Couldn't fix {file}: {exc}{RESET}")
