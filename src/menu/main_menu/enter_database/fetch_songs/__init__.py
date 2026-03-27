from src.navigation.menu_utils import execute_menu_item
from src.debug import slog
from src.library.library import get_song_from_artist_and_name
import questionary
from src.datatables import mp3_categories
from src.menu.song_actions import song_actions

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
        if decision == "Yes":
            song_actions(songs)