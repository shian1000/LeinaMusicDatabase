import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from menu.main_menu import main_menu
from utils.database.app_lock import hold_app_lock
from utils.ui.first_run_prompt import ensure_databases
from utils.ui.sharing_wizard import CONTINUE_SETUP_FLAG, run_sharing_wizard
from utils.ui.startup_tasks import run_startup_tasks

def main():
    # The background request check (auto_resolve_requests.py) never writes
    # while the app is open; one that's running now finishes first
    hold_app_lock(on_wait=lambda: print("Waiting for the automatic request check to finish..."))
    # A fresh install: download the public catalog or start empty (asked
    # until one of them is done) - before the backup and the migrations
    if not ensure_databases():
        return
    # The backup, migrations, the day's tasks and the start-up messages -
    # what's switchable follows Settings -> Start-up tasks
    run_startup_tasks()
    # The sharing setup restarted the app to read the settings it saved
    if CONTINUE_SETUP_FLAG in sys.argv[1:]:
        run_sharing_wizard(resume=True)
    main_menu()

if __name__ == "__main__":
    main()



#TODO On importing songs, note which artists are new