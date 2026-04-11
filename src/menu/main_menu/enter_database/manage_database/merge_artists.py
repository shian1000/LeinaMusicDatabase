import questionary
from utils.database.music_db_manager import get_music_session, Artist
from sqlalchemy import text
import questionary
import time
from utils.database.database_getter import get_artists_from_db_session, extract_artist_info
from utils.debug import slog, mlog
from utils.database.database_sessions import open_database_sessions, close_database_sessions, submit_and_close_database_sessions

def merge_artists_in_db(merge_from, merge_to, sessions):

    """Merge two artists in the database.

    Parameters
    - merge_from: Artist ORM object (the artist to be removed)
    - merge_to: Artist ORM object (the artist to be kept)

    Behavior:
    1. Reassign all songs that reference merge_from.id to merge_to.id
    2. If merge_from has no songs left afterwards, delete the artist row
    """

    slog(merge_from)
    slog(merge_to)

    merge_from_name = getattr(merge_from, "name", str(getattr(merge_from, "id", "?")))
    merge_to_name = getattr(merge_to, "name", str(getattr(merge_to, "id", "?")))

    try:
        from_id = int(merge_from.id)
    except Exception:
        raise ValueError("merge_from must be an Artist-like object with an 'id' attribute")

    try:
        to_id = int(merge_to.id)
    except Exception:
        raise ValueError("merge_to must be an Artist-like object with an 'id' attribute")

    if from_id == to_id:
        print("Source and target artist are the same; nothing to merge.")
        return

    session, tag_session = sessions

    session.execute(
        text("UPDATE songs SET artist_id = :to_id WHERE artist_id = :from_id"),
        {"to_id": to_id, "from_id": from_id}
    )

    remaining = session.execute(
        text("SELECT COUNT(1) FROM songs WHERE artist_id = :from_id"),
        {"from_id": from_id}
    ).scalar()

    if not remaining:
        session.execute(text("DELETE FROM artists WHERE id = :id"), {"id": from_id})
        print(f"Artist {merge_from_name} (id {from_id}) has been merged to {merge_to_name} (id {to_id}).")
    else:
        print(f"Artist {merge_from_name} (id {from_id}) still has {remaining} song(s); not deleting.")



def merge_artists_menu():
    query = input("What querry do you wish to search for: ")

    if query == "":
        return

    sessions = open_database_sessions()
    artists_objects = get_artists_from_db_session("name", query, sessions, aggresive_search=True)
    slog(artists_objects)
    mlog(extract_artist_info(artists_objects, "name, origin"))
    slog(len(artists_objects))

    if len(artists_objects)>1:
        artists_names = extract_artist_info(artists_objects, "name")
        artists_names = [item for t in artists_names for item in t]
        merge_from_name = questionary.select("Select the artist you want to merge from (gets deleted from db)", choices=artists_names).ask()
        merge_to_name = questionary.select("Select the artist you want to merge to (stays in db)", choices=artists_names).ask()
        if merge_from_name == merge_to_name:
            print("Selected the same artists")
            close_database_sessions(sessions)
        else:
            confirm = questionary.confirm(f"You are about to merge all the songs from {merge_from_name} into {merge_to_name}. Do you wish to proceed?").ask()
            if(confirm):
                print("Merging songs")
                merge_from_obj = artists_objects[artists_names.index(merge_from_name)]
                merge_to_obj   = artists_objects[artists_names.index(merge_to_name)]
                merge_artists_in_db(merge_from_obj, merge_to_obj, sessions)
                submit_and_close_database_sessions(sessions)
    else:
        print("Only one artist found")
        close_database_sessions(sessions)

