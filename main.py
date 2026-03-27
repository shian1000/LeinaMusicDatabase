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


#TODO LIST
#[X] Fix Where do you want the files to be copied into?
#[X] Add src to path
#TODO Make passwords a secret
#TODO Make a copy destionation manager
#TODO Make index creator