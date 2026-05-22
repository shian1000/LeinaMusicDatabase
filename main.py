import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from menu.main_menu import main_menu
from tests.test_scripts import test

def main():
    #Comment it to unload tests
    # test()
    main_menu()

if __name__ == "__main__":
    main()



#TODO Make index creator
#TODO Make settings for enabling fetchers. Maybe make them modular?
#[X] Need to rethink/refactor import_data_from_mp3_tags. I need to make it much simpler
##### because right now navigating through that is kind of a nigthmarew
#TODO Kaunas Acoustics : Liūdni slibinai - - Pieno Fabrikėlis
