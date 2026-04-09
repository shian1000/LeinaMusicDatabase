import questionary
from utils.database.music_db_manager import get_music_session, Artist
from sqlalchemy import text
import questionary
import time
from utils.database.database_getter import get_artists_from_db_session, extract_artist_info
from utils.debug import slog
from utils.database.database_sessions import open_database_sessions, close_database_sessions

def merge_artists_in_db(merge_from: str, merge_to: str):
    """
    Reassigns all songs from merge_from artist to merge_to artist,
    then deletes merge_from from the database.
    """
    session = get_music_session()

    # Find both artists
    all_artists = session.query(Artist).all()

    artist_from = next((a for a in all_artists if a.name.lower() == merge_from.lower()), None)
    artist_to = next((a for a in all_artists if a.name.lower() == merge_to.lower()), None)

    if not artist_from:
        print(f"Artist '{merge_from}' not found.")
        session.close()
        return

    if not artist_to:
        print(f"Artist '{merge_to}' not found.")
        session.close()
        return

    print(f"Merging [{artist_from.id}] '{artist_from.name}' → [{artist_to.id}] '{artist_to.name}'")

    session.execute(
        text("UPDATE songs SET artist_id = :to_id WHERE artist_id = :from_id"),
        {"to_id": artist_to.id, "from_id": artist_from.id}
    )

    session.execute(
        text("DELETE FROM artists WHERE id = :id"),
        {"id": artist_from.id}
    )

    session.commit()
    session.close()

    print(f"Done! All songs reassigned and '{artist_from.name}' removed.")

def merge_artists(query):
    sessions = open_database_sessions
    artists_objects = get_artists_from_db_session("name", query, sessions)
    artists_list = extract_artist_info(artists_objects)
    close_database_sessions(sessions)
    slog(artists_list)

    # display_artists(artists_list)

    if len(artists_list)>1:
        merge_from = questionary.select("Select the artist you want to merge from", choices=artists_list).ask()
        merge_to = questionary.select("Select the artist you want to merge to", choices=artists_list).ask()
        if merge_from == merge_to:
            print("Selected the same artists")
        else:
            confirm = questionary.confirm(f"You are about to merge all the songs from {merge_from} into {merge_to}. Do you wish to proceed?").ask()
            if(confirm):
                print("Merging songs")
                merge_artists_in_db(merge_from, merge_to)
    else:
        print("Only one artist found")

