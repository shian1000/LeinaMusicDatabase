import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from menu.main_menu import main_menu
from tests.test_scripts import test
from utils.database.backup import backup_if_needed_today
from utils.database.migrations import run_all_pending_migrations
from utils.database.albums import ALBUM_ID_MIGRATION, run_album_backfill
from utils.database.sharing import share_databases_quietly
from utils.database.files_index import scan_library_quietly
from utils.database.song_references import apply_song_id_changes
from utils.common.debug import slog

def main():
    daily = bool(backup_if_needed_today())
    applied = run_all_pending_migrations()
    # songs.album_id just arrived: link the songs already in the database
    if ALBUM_ID_MIGRATION in applied.get("music", []):
        run_album_backfill()
    # Tags, the library index and users' playlists catch up on songs merged
    # or deleted since (a user DB on an unplugged T7 included)
    try:
        apply_song_id_changes(force=True)
    except Exception as exc:
        slog(f"Updating song references failed (non-fatal): {exc}", priority=3)
    # Once a day, after the backup: refresh the music library index, then
    # the copies the mobile app downloads (files.db included)
    if daily:
        print("Indexing the music library...")
        scan_library_quietly()
        share_databases_quietly()
    #Comment it to unload tests
    # test()
    main_menu()

if __name__ == "__main__":
    main()



#TODO On importing songs, note which artists are new