from utils.debug import slog
from utils.database.database_getter import get_songs_from_db
import questionary
from utils.database.datatables import song_categories
from menu.song_actions import song_actions
from utils.menu_utils import clear_screen
from utils.display_utils import display_songs

def fetch_songs():
    exit_label = ["Back"]
    action_map = song_categories + exit_label

    category = questionary.select("What category do you wish to search for?", choices=action_map).ask()

    if category == exit_label[0]:
        return

    querry = input("What querry do you wish to search for: ")

    if querry == "":
        return

    songs = get_songs_from_db(category, querry)

    display_songs(songs)

    if(songs):
        decision = questionary.confirm("Do you want to do something with these songs?").ask()
        if decision: song_actions(songs)
        else: clear_screen