import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from menu.main_menu import main_menu
from tests.test_scripts import test
from utils.database.backup import backup_if_needed_today
from utils.database.migrations import run_all_pending_migrations
from utils.database.albums import ALBUM_ID_MIGRATION, run_album_backfill
from utils.database.sharing import share_databases_quietly
from utils.database.public_database import PUBLISHED, UNCHANGED, daily_publish_enabled, missing_setup, publish_daily
from utils.database.files_index import scan_library_quietly
from utils.database.song_references import apply_song_id_changes
from utils.database.app_lock import hold_app_lock
from utils.database.inbox_requests import auto_report, count_new_requests, mark_auto_report_shown
from utils.ui.inbox_request_review import show_auto_changes
from utils.database.moods import apply_global_mood_changes_quietly
from utils.ui.ytdlp_update_prompt import offer_ytdlp_update
from utils.ui.first_run_prompt import ensure_databases
from utils.common.debug import slog

def main():
    # The background request check (auto_resolve_requests.py) never writes
    # while the app is open; one that's running now finishes first
    hold_app_lock(on_wait=lambda: print("Waiting for the automatic request check to finish..."))
    # A fresh install: download the public catalog or start empty (asked
    # until one of them is done) - before the backup and the migrations
    if not ensure_databases():
        return
    daily = bool(backup_if_needed_today())
    applied = run_all_pending_migrations()
    # songs.album_id just arrived: link the songs already in the database
    if ALBUM_ID_MIGRATION in applied.get("music", []):
        run_album_backfill()
    # Tags, the library index and users' playlists catch up on songs merged
    # or deleted since (a user DB on an unplugged data drive included)
    try:
        apply_song_id_changes(force=True)
    except Exception as exc:
        slog(f"Updating song references failed (non-fatal): {exc}", priority=3)
    # Songs users with the permission added to / removed from global moods
    # on their phones - before the copy below, so the other phones get them
    mood_changes = apply_global_mood_changes_quietly()
    if mood_changes:
        print(f"Applied {len(mood_changes)} change(s) to global moods from Leina Radio - "
              "Users -> Moods -> Changes from Leina Radio")
    # Once a day, after the backup: refresh the music library index, then
    # the copies the mobile app downloads (files.db included)
    if daily:
        print("Indexing the music library...")
        scan_library_quietly()
        share_databases_quietly()
        # The public copy on GitHub for phones without the server - uploads
        # only when the catalog changed; any failure is tried again tomorrow.
        # Not set up at all (a fresh install: no GITHUB_DB_TOKEN) is no news
        if daily_publish_enabled() and not missing_setup():
            print("Checking the public database on GitHub...")
            published = publish_daily()
            if published is not None and published.status != UNCHANGED:
                colour = "" if published.status == PUBLISHED else "\033[93m"
                print(f"{colour}Public database: {published.message}\033[0m")
        # Once a day too: an old yt-dlp gets blocked by YouTube sooner
        try:
            offer_ytdlp_update()
        except Exception as exc:
            slog(f"yt-dlp update check failed (non-fatal): {exc}", priority=3)
    # What the background request check changed since the last start
    try:
        changes = auto_report(unshown_only=True)
        if changes:
            print(f"Changed automatically from Leina Radio's requests ({len(changes)}):")
            show_auto_changes(changes)
            mark_auto_report_shown()
    except OSError as exc:
        slog(f"Reading the automatic request check's report failed (non-fatal): {exc}", priority=3)
    waiting = count_new_requests()
    if waiting:
        print(f"{waiting} new request(s) in the inbox - Enter database -> Manage database -> Resolve new requests")
    #Comment it to unload tests
    # test()
    main_menu()

if __name__ == "__main__":
    main()



#TODO On importing songs, note which artists are new