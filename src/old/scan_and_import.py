import os
from pathlib import Path
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from utils.database.datatables import Song, Artist
from utils.database.create_tag_db import Tag, SongTag

from sqlalchemy import create_engine, func

import random
import questionary
from sqlalchemy import func




def import_music_from_folder(folder_path: str, mode: str = "skip"):
    """
    Scan a folder (recursively) for mp3 files and import them into music.db.
    Additionally, write folder-based tags into tag.db.
    """

    BASE_DIR = Path(__file__).resolve().parent.parent

    MUSIC_DB_URL = f"sqlite:///{BASE_DIR}/database/music.db"
    TAG_DB_URL = f"sqlite:///{BASE_DIR}/database/tag.db"

    # --- music.db ---
    music_engine = create_engine(MUSIC_DB_URL)
    MusicSession = sessionmaker(bind=music_engine)
    music_session = MusicSession()

    # --- tag.db ---
    tag_engine = create_engine(TAG_DB_URL)
    TagSession = sessionmaker(bind=tag_engine)
    tag_session = TagSession()

    folder = Path(folder_path).resolve()
    if not folder.exists():
        print(f"❌ Folder {folder_path} does not exist.")
        return

    mp3_files = list(folder.rglob("*.mp3"))
    print(f"Found {len(mp3_files)} mp3 files.")

    added_count = 0
    updated_count = 0

    for file in mp3_files:
        try:
            audio = MP3(file)
            easy = EasyID3(file)

            title = easy.get("title", ["Unknown Title"])[0]
            artist_name = easy.get("artist", ["Unknown Artist"])[0]
            album = easy.get("album", [None])[0]
            year = easy.get("date", [None])[0]

            # --- origin from COMMENT tag ---
            origin = None
            if audio.tags:
                comments = audio.tags.getall("COMM")
                if comments and comments[0].text:
                    origin = comments[0].text[0]

            # --- language detection ---
            language = easy.get("language", ["Unknown"])[0]

            if language == "Unknown" and audio.tags:
                txxx_tags = audio.tags.getall("TXXX")
                for tag in txxx_tags:
                    if tag.desc.lower() == "language" and tag.text:
                        language = tag.text[0]
                        break


            print(f"Checking: {artist_name} - {title} (lang: {language})")

            # --- artist ---
            artist = resolve_artist(music_session, artist_name, origin)


            # --- song ---
            song = music_session.query(Song).filter(
                Song.title == title,
                Song.artist_id == artist.id
            ).first()

            if song:
                if mode == "skip":
                    print(" -> Song exists, skipping.")
                    continue

                elif mode == "update":
                    song.year = int(year) if year and str(year).isdigit() else song.year
                    song.language = language
                    song.album = album or song.album
                    music_session.commit()
                    updated_count += 1
                    print(" -> Song updated.")


            else:
                song = Song(
                    title=title,
                    artist_id=artist.id,
                    album=album,
                    year=int(year) if year and str(year).isdigit() else None,
                    language=language,
                    nostalgic=0,
                    melancholic=0,
                    party=0
                )
                music_session.add(song)
                music_session.commit()
                added_count += 1
                print(" -> Added new song.")

            # ------------------------
            # ✅ FOLDER TAGGING LOGIC
            # ------------------------

            relative_folder = file.parent.relative_to(folder)
            tag_name = f"{relative_folder}"

            tag = tag_session.query(Tag).filter_by(name=tag_name).first()
            if not tag:
                tag = Tag(name=tag_name)
                tag_session.add(tag)
                tag_session.commit()

            link_exists = tag_session.query(SongTag).filter_by(
                song_id=song.id,
                tag_id=tag.id
            ).first()

            if not link_exists:
                tag_session.add(SongTag(song_id=song.id, tag_id=tag.id))
                tag_session.commit()
                print(f" -> Tagged with [{tag_name}]")

        except Exception as e:
            print(f"⚠ Error reading {file}: {e}")

    music_session.close()
    tag_session.close()

    print(f"\n🎉 Done! Added {added_count}, updated {updated_count}.")



def resolve_artist(music_session, artist_name: str, origin: str | None = None) -> Artist:
    """
    Resolve artist case-insensitively.
    If multiple artists match, ask the user to choose one,
    showing sample songs for each.
    """

    # Case-insensitive match
    matching_artists = music_session.query(Artist).filter(
        func.lower(Artist.name) == artist_name.lower()
    ).all()

    # --- no artist found ---
    if not matching_artists:
        artist = Artist(
            name=artist_name,
            origin=origin
        )
        music_session.add(artist)
        music_session.commit()
        return artist


    # --- exactly one artist ---
    if len(matching_artists) == 1:
        return matching_artists[0]

    # --- multiple artists found: user must choose ---
    choices = []

    for artist in matching_artists:
        # fetch up to 3 random songs
        songs = (
            music_session.query(Song)
            .filter(Song.artist_id == artist.id)
            .all()
        )

        sample_songs = random.sample(songs, min(3, len(songs))) if songs else []

        if sample_songs:
            song_preview = ", ".join(song.title for song in sample_songs)
        else:
            song_preview = "no songs yet"

        label = f"{artist.name} (id={artist.id}) → {song_preview}"
        choices.append(questionary.Choice(title=label, value=artist))

    selected_artist = questionary.select(
        f"Multiple artists found for '{artist_name}'. Choose the correct one:",
        choices=choices
    ).ask()

    return selected_artist

