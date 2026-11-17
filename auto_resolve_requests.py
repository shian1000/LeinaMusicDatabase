"""The automation, run every INBOX_AUTO_TICK by the musicdatabase-inbox systemd
user timer (installed with Settings -> Server -> "Setup this PC for database
sharing"). Each run does only what Settings -> "Automation"'s intervals make
due (utils/common/automation_settings.py), in this order:
1. the automatic pass over new requests (utils/database/inbox_auto.py), by
   the built-in rules turned on there and the owner's own ("My rules") -
   which may also add a song Leina Radio asked for;
2. Manage database -> "Run all"'s steps, nobody asked (run_unattended_steps())
   - each list takes what its rule allows, the rest waits for the owner: the
   clean-ups, which need no internet, only when the databases or the settings
   changed since their last run; the lookups (spell check, albums, years,
   genres, lyrics) on their own interval, only while MusicBrainz answers;
3. the copy for Leina Radio, when a database changed;
4. the public database on GitHub (public_database.publish_if_due()).
What 1 and 2 changed is shown at the app's next start (Inbox/auto_report.json).
Does nothing while the app is open (the app's own "Resolve new requests" runs
the same pass, and it publishes at start when due), while the data drive
isn't mounted, or while music.db waits for a migration the app hasn't
applied. An app starting during a run makes the steps stop after the song
they're on (app_lock.app_waiting()) and the run end without the GitHub
publish; each lookup step stops after Settings -> "Automation"'s minutes
anyway, keeping what it found.
Output goes to the journal: journalctl --user -u musicdatabase-inbox"""

import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from config.constants import AUTOMATION_CONFIG_FILE, RUN_ALL_CONFIG_FILE, SANITIZE_CONFIG_FILE
from utils.common.automation_settings import due, files_changed, mark_run
from utils.database.app_lock import app_waiting, try_exclusive_lock
from utils.database.backup import DB_FILES
from utils.database.inbox_requests import new_request_paths

# A change to any of these can give the clean-ups something new to do
CLEAN_UP_INPUTS = (DB_FILES["music"], DB_FILES["tag"], AUTOMATION_CONFIG_FILE, RUN_ALL_CONFIG_FILE,
                   SANITIZE_CONFIG_FILE)


def _resolve_requests() -> None:
    from utils.database.inbox_auto import auto_resolve
    from utils.database.inbox_requests import read_new_requests
    from utils.database.music_db_manager import get_music_session

    from utils.database.database_sessions import close_global_database_sessions

    session = get_music_session()
    try:
        result = auto_resolve(session, read_new_requests())
    finally:
        session.close()
        # Opened by the import when a rule of the owner's added a requested song
        close_global_database_sessions()
    for request in result.rejected:
        print(f"Rejected {request.name}: {request.error}")
    for change in result.changes:
        print(f"Changed: {change['text']}")
    print(f"{len(result.moved)} file(s) done, {len(result.left)} left for review "
          f"({result.to_review} case(s)).")


def _online() -> bool:
    """Whether the lookups can reach the internet: offline, each would end as
    "nothing found" and be left out for days (automation_settings.remember_misses())."""
    try:
        socket.create_connection(("musicbrainz.org", 443), timeout=10).close()
        return True
    except OSError:
        return False


def _run_steps(clean_ups: bool, lookups: bool) -> None:
    import menu.main_menu  # noqa: F401 - before the menu package below (circular import)
    from menu.main_menu.enter_database.manage_database import CLEAN_UP_STEPS, LOOKUP_STEPS, run_unattended_steps
    from musicdatabase_core.file_tools import now_iso
    from utils.database.database_sessions import close_global_database_sessions, open_and_set_global_database_sessions
    from utils.database.inbox_requests import InboxFileError, add_auto_report

    open_and_set_global_database_sessions()
    try:
        taken = run_unattended_steps({*(CLEAN_UP_STEPS if clean_ups else ()), *(LOOKUP_STEPS if lookups else ())})
    finally:
        close_global_database_sessions()
    at = now_iso()
    for step, item in taken:
        print(f"Changed: {step}: {item}")
    print(f"Run all steps: {len(taken)} change(s) by the rules.")
    try:
        add_auto_report([{"at": at, "kind": "clean_up", "text": f"{step}: {item}"} for step, item in taken])
    except InboxFileError as exc:  # the changes are saved already; only the report is missing
        print(exc)


def main() -> int:
    from utils.database.public_database import publish_due

    requests = due("requests") and bool(new_request_paths())
    clean_ups = due("clean_ups") and files_changed("clean_ups", CLEAN_UP_INPUTS)
    lookups = due("lookups")
    github = publish_due()
    if not (requests or clean_ups or lookups or github):
        return 0
    lock = try_exclusive_lock()
    if lock is None:
        print("MusicDatabase is open - the automation waits until it's closed; skipped.")
        return 0
    try:
        from utils.database.migrations import has_pending_migrations
        if has_pending_migrations("music"):
            print("music.db waits for a migration - start MusicDatabase once first; skipped.")
            return 0
        databases = {path: path.stat().st_mtime_ns if path.exists() else None for path in DB_FILES.values()}
        if requests:
            mark_run("requests")
            _resolve_requests()
        if lookups:
            mark_run("lookups")
            if not _online():
                print("MusicBrainz doesn't answer - the lookups wait for the next interval.")
                lookups = False
        if clean_ups or lookups:
            _run_steps(clean_ups, lookups)
        if clean_ups:
            mark_run("clean_ups", files=CLEAN_UP_INPUTS)
        # The phones should see the changes without waiting a day
        if any((path.stat().st_mtime_ns if path.exists() else None) != stamp for path, stamp in databases.items()):
            from utils.database.sharing import share_databases_quietly
            share_databases_quietly()
        # The app publishes at its start when it's due: it doesn't wait for an upload
        if github and not app_waiting():
            from utils.database.public_database import publish_if_due
            published = publish_if_due()
            if published is not None:
                print(f"Public database on GitHub: {published.status}: {published.message}")
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
