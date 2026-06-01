from utils.database.music_db_manager import get_music_session, Song, Artist
from sqlalchemy import text
import questionary
from utils.database.database_management import edit_db_entry
from utils.common.text_utils import compare_strings
from utils.database.database_sessions import get_global_database_sessions
from utils.database.tags_management import has_tag_on_song

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
        key_origin = artist.origin
        if key in seen:
            seen_origin = seen[key].origin
            if (key_origin is None or seen_origin is None or 
                    key_origin.lower() == seen_origin.lower()):
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

        session.execute(
            text("UPDATE songs SET artist_id = :original_id WHERE artist_id = :duplicate_id"),
            {"original_id": original.id, "duplicate_id": duplicate.id}
        )

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




def resolve_duplicated_artists():
    remove_duplicate_songs()
    remove_duplicate_artists()
    remove_duplicate_songs()
    remove_duplicate_artists()

def resolve_duplicated_albums():
    sessions = get_global_database_sessions()
    music_session, _ = sessions

    # fetch all songs that have a non-empty album
    all_songs = (
        music_session
        .query(Song)
        .join(Artist)
        .filter(Song.album != None)
        .filter(Song.album != "")
        .order_by(Artist.name, Song.title)
        .all()
    )

    # cluster albums by fuzzy compare_strings matching
    clusters = []  # each cluster: {'key': representative_album, 'songs': [Song,...], 'albums': set()}
    for s in all_songs:
        album = (s.album or "").strip()
        if not album or album == "Singles":
            continue
        placed = False
        for cl in clusters:
            if compare_strings(album, cl['key']):
                cl['songs'].append(s)
                cl['albums'].add(album)
                placed = True
                break
        if not placed:
            clusters.append({'key': album, 'songs': [s], 'albums': {album}})

    # select clusters with songs from more than one artist
    target_clusters = [c for c in clusters if len({song.artist.id for song in c['songs']}) > 1]
    if not target_clusters:
        print("No duplicated albums found.")
        return

    for cl in target_clusters:
        songs_list = list(cl['songs'])
        rep_album = cl['key']
        # loop until duplicates resolved or user done
        while True:
            # group remaining songs by artist
            artists_map = {}
            for s in songs_list:
                artists_map.setdefault(s.artist.id, []).append(s)

            if len(artists_map) <= 1:
                break

            choices = []
            id_to_artist = {}
            for artist_id, slist in artists_map.items():
                artist_name = slist[0].artist.name
                # collect the album text(s) as stored for this artist in the cluster
                album_vals = sorted({(s.album or "").strip() for s in slist if (s.album or "").strip()})
                if album_vals:
                    album_repr = "; ".join(album_vals)
                    label = f"{artist_name} — \"{album_repr}\" — {len(slist)} song(s)"
                else:
                    label = f"{artist_name} — {len(slist)} song(s)"
                choices.append(label)
                id_to_artist[label] = artist_id

            choices.append("Done")

            answer = questionary.select(
                f"Album cluster '{rep_album}' appears for multiple artists. From which artist should the album be removed?",
                choices=choices,
            ).ask()

            if not answer or answer == "Done":
                break

            sel_artist_id = id_to_artist.get(answer)
            sel_songs = artists_map.get(sel_artist_id, [])

            # Remove album value from selected artist's songs in this cluster
            for song in sel_songs:
                edit_db_entry(song, "album", "")
                # remove from local list so it's not considered in further iterations
                if song in songs_list:
                    songs_list.remove(song)