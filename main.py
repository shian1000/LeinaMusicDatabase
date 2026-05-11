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
#[X] Find album duplicates
#[X] Sprawdzić, czy google_search_fetcher.py działa z fill_missing albums i globalnym otwieraniem drivera
#[X] Ogarnąć genius_fetcher.py
#TODO Make settings for enabling fetchers. Maybe make them modular?
#[X] Make blacklist external
#[X] Czasem się kopiowanie do schowka rypie przy spell checking
#TODO To prostackie, że dalej nie zapisuję deubug do pliku. Trzeba to zmienić
#TODO Remove spellcheck and rubbish tags upon editting
#TODO Poradzić coś na uknown artist
#[X] Rozdzielić Becka i becka