"""The automatic pass over new requests from the phone, run every
INBOX_AUTO_INTERVAL by the musicdatabase-inbox systemd user timer (installed
with Settings -> Server -> "Setup this PC for database sharing"). Logic:
src/utils/database/inbox_auto.py. Does nothing while the app is open (the
app's own "Resolve new requests" runs the same pass), while T7 isn't
mounted, or while music.db waits for a migration the app hasn't applied.
Output goes to the journal: journalctl --user -u musicdatabase-inbox"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from utils.database.app_lock import try_exclusive_lock
from utils.database.inbox_requests import new_request_paths


def main() -> int:
    if not new_request_paths():
        return 0
    lock = try_exclusive_lock()
    if lock is None:
        print("MusicDatabase is open - its own \"Resolve new requests\" handles the inbox; skipped.")
        return 0
    try:
        from utils.database.migrations import has_pending_migrations
        if has_pending_migrations("music"):
            print("music.db waits for a migration - start MusicDatabase once first; skipped.")
            return 0
        from utils.database.inbox_auto import auto_resolve
        from utils.database.inbox_requests import read_new_requests
        from utils.database.music_db_manager import get_music_session
        from utils.database.sharing import share_databases_quietly

        session = get_music_session()
        try:
            result = auto_resolve(session, read_new_requests())
        finally:
            session.close()
        for request in result.rejected:
            print(f"Rejected {request.name}: {request.error}")
        for change in result.changes:
            print(f"Changed: {change['text']}")
        print(f"{len(result.moved)} file(s) done, {len(result.left)} left for review "
              f"({result.to_review} case(s)).")
        # The phones should see the fixed links / values, and the videos
        # found bad, without waiting a day
        if result.changes or result.check_stats.get("marked"):
            share_databases_quietly()
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
