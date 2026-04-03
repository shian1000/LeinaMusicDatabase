from utils.database.database_getter import get_songs, get_artists
import questionary
from utils.database.datatables import song_categories, artist_categories

def search_for_songs(category: str = ""):
    action_map = song_categories

    if category == "":
        category = questionary.select("What category do you wish to search for?", choices=action_map).ask()

    querry = input("What querry do you wish to search for: ")

    if querry == "":
        return

    songs = get_songs(category, querry)

    return songs

def search_for_artists():

    querry = input("What querry do you wish to search for: ")

    if querry == "":
        return

    artists = get_artists("Artist", querry)

    print(artists)

    return artists