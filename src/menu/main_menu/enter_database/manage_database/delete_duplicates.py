from utils.database.music_db_manager import get_music_session, Song, Artist
from sqlalchemy import text
import questionary

def remove_duplicate_songs():
    """
    Finds and removes duplicate songs (same artist_id + title),
    keeping the entry with the lowest id.
    """
    session = get_music_session()

    all_songs = session.query(Song).join(Artist).order_by(Artist.name, Song.title).all()

    seen = {}
    duplicates = []

    for song in all_songs:
        key = (song.artist_id, song.title.lower())
        if key in seen:
            duplicates.append(song)
        else:
            seen[key] = song

    if not duplicates:
        print("No duplicates found.")
        session.close()
        return

    print(f"Found {len(duplicates)} duplicate(s):")
    for song in duplicates:
        print(f"  - [{song.id}] {song.artist.name} — {song.title}")

    for song in duplicates:
        session.delete(song)

    session.commit()
    session.close()
    print(f"\nRemoved {len(duplicates)} duplicate(s).")


def remove_duplicate_artists():
    session = get_music_session()

    all_artists = session.query(Artist).order_by(Artist.name).all()

    seen = {}
    duplicates = []

    for artist in all_artists:
        key = artist.name.lower()
        if key in seen:
            duplicates.append((seen[key], artist))
        else:
            seen[key] = artist

    if not duplicates:
        print("No duplicate artists found.")
        session.close()
        return

    print(f"Found {len(duplicates)} duplicate(s):")

    for original, duplicate in duplicates:
        print(f"  - Keeping [{original.id}] '{original.name}', removing [{duplicate.id}]")

        # Reassign songs using raw SQL to avoid ORM flush issues
        session.execute(
            text("UPDATE songs SET artist_id = :original_id WHERE artist_id = :duplicate_id"),
            {"original_id": original.id, "duplicate_id": duplicate.id}
        )

        # Count and print reassigned songs
        songs = session.query(Song).filter(Song.artist_id == original.id).all()
        for song in songs:
            print(f"    -> Reassigned song '{song.title}' to artist [{original.id}]")

        session.execute(
            text("DELETE FROM artists WHERE id = :id"),
            {"id": duplicate.id}
        )

    session.commit()
    session.close()
    print(f"\nRemoved {len(duplicates)} duplicate artist(s).")




def delete_duplicates():
    remove_duplicate_songs()
    remove_duplicate_artists()
    remove_duplicate_songs()
    remove_duplicate_artists()