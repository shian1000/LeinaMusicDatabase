import os
from pathlib import Path
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.datatables import Song, Artist, Base


def import_music_from_folder(folder_path: str, mode: str = "skip"):
    """
    Scan a folder for mp3 files and import them into the database.

    Parameters:
        folder_path: path to the folder to scan
        mode: "skip" (default) or "update"
    """
    BASE_DIR = Path(__file__).resolve().parent.parent
    DATABASE_URL = f"sqlite:///{BASE_DIR}/database/music.db"

    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    folder = Path(folder_path)
    if not folder.exists():
        print(f"❌ Folder {folder_path} does not exist.")
        return

    mp3_files = list(folder.rglob("*.mp3"))
    print(f"Found {len(mp3_files)} mp3 files.")

    added_count = 0
    updated_count = 0

    for file in mp3_files:
        try:
            audio = MP3(file, ID3=EasyID3)
            title = audio.get("title", ["Unknown Title"])[0]
            artist_name = audio.get("artist", ["Unknown Artist"])[0]
            year = audio.get("date", [0])[0]

            # --- language detection ---
            language = audio.get("language", ["Unknown"])[0]
            if language == "Unknown":
                txxx_tags = audio.tags.getall("TXXX")
                for tag in txxx_tags:
                    if tag.desc.lower() == "language":
                        language = tag.text[0]
                        break

            print(f"Checking: {artist_name} - {title} (lang: {language})")

            # Check if artist exists
            artist = session.query(Artist).filter(Artist.name == artist_name).first()
            if not artist:
                artist = Artist(name=artist_name)
                session.add(artist)
                session.commit()

            # Check if song exists
            existing_song = session.query(Song).filter(
                Song.title == title,
                Song.artist_id == artist.id
            ).first()

            if existing_song:
                if mode == "skip":
                    print(" -> Song already exists, skipping.")
                    continue
                elif mode == "update":
                    # Update metadata
                    existing_song.year = int(year) if str(year).isdigit() else existing_song.year
                    existing_song.language = language
                    session.commit()
                    updated_count += 1
                    print(" -> Song exists, updated!")
                    continue

            # Create new song record
            new_song = Song(
                title=title,
                artist_id=artist.id,
                year=int(year) if str(year).isdigit() else None,
                language=language,
                nostalgic=0,
                melancholic=0,
                party=0
            )
            session.add(new_song)
            session.commit()
            added_count += 1
            print(" -> Added!")

        except Exception as e:
            print(f"⚠ Error reading {file}: {e}")

    print(f"\n🎉 Done! Added {added_count} new songs, updated {updated_count} existing songs.")



    print(f"\n🎉 Done! Added {added_count} new songs.")
