import questionary
from menu.main_menu.enter_database.manage_database.manual_management import edit_entry_menu
from utils.database.datatables import Song, Artist
from utils.debug import slog

submition_list = []

def edit_songs_menu(songs_objects, sessions = None):
    songs_list = []
    slog(songs_objects)
    for i, song in enumerate(songs_objects):
        songs_list.append(f"{song.artist.name} - {song.title} ({song.album})")
    print(songs_list)

    submit_option = "Submit and save"
    songs_list.append(submit_option)
    loop_running = True
    while loop_running:
        song_selection = questionary.select("Select the entity you want to edit", choices=songs_list).ask()
        if song_selection == submit_option:
            loop_running = False
            return
        else:
            index = songs_list.index(song_selection)
            edit_entry_menu(db_object=songs_objects[index], sessions=sessions)