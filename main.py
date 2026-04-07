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



#TODO Niech search_songs_from_name będzie częścią get_songs
#TODO W manual_management w edit_entry zaraz po wstukaniu nazwy utworu, powinniśmy mieć możliwość pickowania utworu, jeżeli jest więcej niż jeden i otrzymania listy
#TODO datatables - zabrać stamtąd name, ale żeby i tak wszystko działało. Może zrobię osobny datatables na ukryte kategorie?


#TODO LIST
#[X] Fix Where do you want the files to be copied into?
#[X] Add src to path
#[X] Make passwords a secret
#[X] Make a copy destionation manager
#TODO Make index creator
#TODO Wyszukaj w języku angielskim - będzie song Łąki Łan not found
#TODO Spring cleaning:
#[X] main.py
#--- main_menu
#--- enter_database
#--- manage_local_files
#--- execute_menu_item


#navigation
#[X] menu_utils