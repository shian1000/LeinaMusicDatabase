import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from menu.main_menu import main_menu
from test_scripts import test

def main():
    #Comment it to unload tests
    # test()
    main_menu()

if __name__ == "__main__":
    main()



#TODO Make index creator
#TODO Find album duplicates
#TODO Fetch missing results - right away option to edit the results using questionary
#TODO Czego kopiowanie do schowka czasem działa, a czasem nie