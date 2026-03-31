from navigation.menu_utils import execute_menu_item
from debug import slog
from library.library import get_song_from_artist_and_name
import questionary
from datatables import mp3_categories
from menu.song_actions import song_actions
from navigation.menu_utils import clear_screen

def fetch_songs():
    exit_label = ["Back"]
    action_map = mp3_categories + exit_label

    category = questionary.select("What category do you wish to search for?", choices=action_map).ask()

    if category == exit_label[0]:
        return

    querry = input("What querry do you wish to search for: ")

    if querry == "":
        return

    songs = get_song_from_artist_and_name(category, querry)

    if(songs):
        decision_map = ["Yes", "No"]
        decision = questionary.select("Do you want to do something with these songs?", choices=decision_map).ask()
        if decision == "No": clear_screen()
        if decision == "Yes":
            song_actions(songs)