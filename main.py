import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from menu.main_menu import main_menu
from tests.test_scripts import test
from utils.database.backup import backup_if_needed_today
from utils.database.migrations import run_all_pending_migrations

def main():
    backup_if_needed_today()
    run_all_pending_migrations()
    #Comment it to unload tests
    # test()
    main_menu()

if __name__ == "__main__":
    main()



#TODO Make index creator
#TODO On importing songs, note which artists are new