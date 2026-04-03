from utils.database.datatables import song_categories
import questionary
from src.library.library import edit_song_entry, search_songs_from_name

def edit_entry():
    action_map = song_categories

    query = input("What song do you wish to search for: ")

    category = questionary.select("What category entity do you wish to edit?", choices=action_map).ask()

    new_data = input("Type new data: ")

    if query == "":
        return
    
    songs = search_songs_from_name(query)

    songs_simple = [f"{artist} - {title}" for artist, title in songs]

    if len(songs) > 1:
        print("More than 1")
        song = questionary.select("Found more than one result:", choices=songs_simple).ask()
    else:
        song = songs_simple[0]
        print("Probably fine")

    print(f"This be ur song: {song}")

    edit_song_entry(song, category, new_data)