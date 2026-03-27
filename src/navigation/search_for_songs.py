from src.library.library import get_song_from_artist_and_name
import questionary
from src.datatables import mp3_categories

def search_for_songs():
    action_map = mp3_categories

    category = questionary.select("What category do you wish to search for?", choices=action_map).ask()

    querry = input("What querry do you with to search for: ")

    if querry == "":
        return

    songs = get_song_from_artist_and_name(category, querry)