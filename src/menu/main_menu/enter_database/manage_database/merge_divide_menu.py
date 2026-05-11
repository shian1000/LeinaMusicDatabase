import questionary
import time
from utils.database.database_getter import get_artists_from_db_session, extract_db_object_info
from utils.debug import slog, mlog
from utils.database.database_sessions import submit_global_database_session, get_global_database_sessions
from utils.database.database_management import merge_artists_in_db, divide_artist
from utils.database.datatables import artist_categories, Song, song_categories
from rich import print
from questionary import Style
from utils.menu_utils import pick_from_db_objects, ask_for_entires_list
from utils.database.database_getter import get_songs_from_db_session
from utils.database.tags_management import has_tag_on_song


def merge_artists_menu(artist_objects = None):
    red = Style([('question', 'fg:red bold')])
    green = Style([('question', 'fg:green bold')])
    q0 = ("Select the artist you want to merge from (gets deleted from db)")
    q1 = ("Select the artist you want to merge to (stays in db)")

    if not artist_objects:
        artist_objects = ask_for_entires_list(mode = "Artist", allow_one_result=False)
        if not artist_objects:
            return
        

    songs_names = []

    for obj in artist_objects:
        artists_songs = get_songs_from_db_session(song_categories[7], obj.id)
        
        this_artist_songs = []

        for song in artists_songs:
            this_artist_songs.append(song.title)

        songs_names.append(this_artist_songs)

    print(songs_names)



    obj1 = pick_from_db_objects(artist_objects, question=q0, additional_info=songs_names, style=red)
    if not obj1:
        return
    obj2 = pick_from_db_objects(artist_objects, question=q1, additional_info=songs_names, style=green)
    if not obj1:
        return
    if obj1 == obj2:
        print("Picked the same artists")
        return
    
    confirm = questionary.confirm(
        f"You are about to merge all the songs from {obj1.name} into {obj2.name}. Do you wish to proceed?"
    ).ask()
    if confirm:
        print("Merging songs")
        merge_artists_in_db(obj1, obj2)
        submit_global_database_session()
    




def divide_artists_menu():
    q0 = ("Select the artist you want to divide")

    artist_objects = ask_for_entires_list(mode = "Artist")
    if not artist_objects:
        return
    
    obj = pick_from_db_objects(artist_objects, question=q0)

    music_session, _ = get_global_database_sessions()
    artist_id = int(obj.id)
    songs = (
        music_session
        .query(Song)
        .filter(Song.artist_id == artist_id)
        .order_by(Song.id)
        .all()
    )

    if not songs:
        print(f"Artist '{obj.name}' (id {artist_id}) has no songs. Nothing to do.")
        return

    print(f"Found {len(songs)} song(s) for artist '{obj.name}':")
    for idx, s in enumerate(songs, start=1):
        album = (s.album or "").strip()
        year = s.year or ""
        extra = []
        if album:
            extra.append(f"album: {album}")
        if year:
            extra.append(f"year: {year}")
        extra_str = f" ({'; '.join(extra)})" if extra else ""
        print(f"  {idx}. [{s.id}] {s.title}{extra_str}")

    if len(songs) == 1:
        print(f"Artist has only one song. Nothing to do.")
        return

    confirmation = questionary.confirm("Proceed with dividing this artist so each song gets its own new artist record?").ask()
    if confirmation:
        divide_artist(obj)        
    else:
        print("Aborted by user.")
        return

    print(q0)
    print()