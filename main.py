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
#[X] Wyszukiwanie - spróbuj szukać bez feat Daft Punk - Lose Yourself to Dance (feat. Pharrell Williams), Depeche Mode - Heroes (Highline Session Version)
#[X] Czarna lista - nie bierz pod uwagę słowa, jeżeli nie zostało oddzielone spacją David Tobin,Jeff Meegan, Charley Harrison - So Much To Live For Today
#[X] Wyjebuje się kiedy misstypying przy manual management
#[X] Fetch songs - make it look in other categories too
#[X] fetch_songs if decision: song_actions(songs_objects, sessions) - make it not need sessions
#[X] fill_missing_ablums: make it use edit_songs_menu.py
#[X] pick_from_db_objects - is this needed?