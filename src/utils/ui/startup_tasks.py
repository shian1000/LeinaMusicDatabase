"""What main.py does on every start before the main menu, in this order. The
switchable steps follow Settings -> "Start-up tasks" (utils/common/
startup_settings.py, all on by default); the rest always runs - see that
module for which and why."""
from utils.common import startup_settings as switches
from utils.common.debug import slog
from utils.common.output import RESET, YELLOW



def _daily() -> bool:
    """Whether this is the day's first start, taking the daily backup when it's on."""
    from utils.database.backup import BackupFailedError, backup_if_needed_today, newest_backup_time

    if switches.enabled(switches.BACKUP):
        # The day's first backup has always marked the first start; the marker
        # keeps turning the backup off later the same day from running them twice
        try:
            daily = bool(backup_if_needed_today())
        except BackupFailedError as exc:
            print(f"{YELLOW}The daily backup of the databases failed: {exc}. It's tried again at the next "
                  f"start - or now from Settings -> Server -> Back up database now.{RESET}")
            return switches.first_start_today()
        if daily:
            switches.mark_daily_tasks_run()
        return daily
    warning = switches.backup_warning(newest_backup_time())
    if warning:
        print(f"{YELLOW}{warning}{RESET}")
    return switches.first_start_today()


def _keep_data_consistent() -> None:
    from utils.database.albums import ALBUM_ID_MIGRATION, run_album_backfill
    from utils.database.inbox_song_requests import SETTLED_REQUESTS_MIGRATION, run_settled_requests_backfill
    from utils.database.migrations import run_all_pending_migrations
    from utils.database.song_references import apply_song_id_changes

    applied = run_all_pending_migrations()
    # songs.album_id just arrived: link the songs already in the database
    if ALBUM_ID_MIGRATION in applied.get("music", []):
        run_album_backfill()
    # settled_song_requests just arrived: the requests settled before it
    if SETTLED_REQUESTS_MIGRATION in applied.get("music", []):
        run_settled_requests_backfill()
    # Tags, the library index and users' playlists catch up on songs merged
    # or deleted since (a user DB on an unplugged data drive included)
    try:
        apply_song_id_changes(force=True)
    except Exception as exc:
        slog(f"Updating song references failed (non-fatal): {exc}", priority=3)


def _apply_mood_changes() -> None:
    from utils.database.moods import apply_global_mood_changes_quietly

    mood_changes = apply_global_mood_changes_quietly()
    if mood_changes:
        print(f"Applied {len(mood_changes)} change(s) to global moods from Leina Radio - "
              "Users -> Moods -> Changes from Leina Radio")


def _publish_to_github() -> None:
    from utils.database.public_database import PUBLISHED, UNCHANGED, publish_due, publish_if_due

    # The public copy on GitHub for phones without the server - uploads only
    # when the catalog changed; any failure is tried again an interval later
    # (Settings -> Automation). Not set up at all (a fresh install: no
    # GITHUB_DB_TOKEN) is no news
    if not publish_due():
        return
    print("Checking the public database on GitHub...")
    published = publish_if_due()
    if published is not None and published.status != UNCHANGED:
        colour = "" if published.status == PUBLISHED else YELLOW
        print(f"{colour}Public database: {published.message}{RESET}")


def _offer_ytdlp_update() -> None:
    from utils.ui.ytdlp_update_prompt import offer_ytdlp_update

    # An old yt-dlp gets blocked by YouTube sooner
    try:
        offer_ytdlp_update()
    except Exception as exc:
        slog(f"yt-dlp update check failed (non-fatal): {exc}", priority=3)


def _show_messages() -> None:
    from utils.database.inbox_requests import InboxFileError, auto_report, count_new_requests, mark_auto_report_shown
    from utils.ui.inbox_request_review import show_auto_changes

    # What the background request check changed since the last start
    try:
        changes = auto_report(unshown_only=True)
        if changes:
            print(f"Changed automatically - Leina Radio's requests and the clean-ups, Settings -> "
                  f"Automation ({len(changes)}):")
            show_auto_changes(changes)
            mark_auto_report_shown()
    except InboxFileError as exc:
        print(f"{YELLOW}{exc}{RESET}")
    except OSError as exc:
        slog(f"Reading the automatic request check's report failed (non-fatal): {exc}", priority=3)
    waiting = count_new_requests()
    if waiting:
        print(f"{waiting} new request(s) in the inbox - Enter database -> Manage database -> Resolve new requests")


def _index_library() -> None:
    from utils.database.files_index import scan_library_quietly

    print("Indexing the music library...")
    scan_library_quietly()


def _share_databases() -> None:
    from utils.database.sharing import share_databases_quietly

    share_databases_quietly()


def run_startup_tasks() -> None:
    daily = _daily()
    _keep_data_consistent()
    # Before the copy below, so the other phones get the changes
    if switches.enabled(switches.MOOD_CHANGES):
        _apply_mood_changes()
    # Once a day, after the backup: the library index first, then the copies
    # the mobile app downloads (files.db included)
    if daily:
        if switches.enabled(switches.LIBRARY_INDEX):
            _index_library()
        if switches.enabled(switches.SHARE_COPY):
            _share_databases()
    # When its interval is up (a day by default), also between the timer's runs
    _publish_to_github()
    if daily and switches.enabled(switches.YTDLP_UPDATE):
        _offer_ytdlp_update()
    if switches.enabled(switches.MESSAGES):
        _show_messages()
