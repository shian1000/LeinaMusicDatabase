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

import time
from settings import settings
from utils.common.debug import slog

import unicodedata



def setup_sessions():
    BASE_DIR = settings.database_dir
    music_engine = create_engine(f"sqlite:///{BASE_DIR}/music.db")
    tag_engine = create_engine(f"sqlite:///{BASE_DIR}/tag.db")
    music_session = sessionmaker(bind=music_engine)()
    tag_session = sessionmaker(bind=tag_engine)()
    return music_session, tag_session


def extract_mp3_metadata(file: Path) -> dict:
    audio = MP3(file)
    easy = EasyID3(file)

    title = easy.get("title", ["Unknown Title"])[0]
    artist_name = easy.get("artist", ["Unknown Artist"])[0]
    album = easy.get("album", [None])[0]
    year = easy.get("date", [None])[0]
    language = easy.get("language", ["Unknown"])[0]

    origin = None
    if audio.tags:
        comments = audio.tags.getall("COMM")
        if comments and comments[0].text:
            origin = comments[0].text[0]

    if language == "Unknown" and audio.tags:
        for tag in audio.tags.getall("TXXX"):
            if tag.desc.lower() == "language" and tag.text:
                language = tag.text[0]
                break

    return {
        "title": title,
        "artist_name": artist_name,
        "album": album,
        "year": year,
        "language": language,
        "origin": origin,
    }

def normalize_title(title: str) -> str:
    title = unicodedata.normalize("NFC", title)
    title = title.strip().lower()
    return title

def upsert_song(music_session, artist, metadata: dict, mode: str):
    title = metadata["title"]
    album = metadata["album"]
    language = metadata["language"]
    year = int(metadata["year"]) if metadata["year"] and str(metadata["year"]).isdigit() else None

    print(f"Checking: {artist.name} - {title} (lang: {language})")

    slog(f"Artist: '{artist.name}' (id={artist.id})")
    songs = music_session.query(Song).filter(
        Song.artist_id == artist.id
    ).all()

    normalized_title = normalize_title(title)

    slog(f"Looking for title: '{title}' (normalized: '{normalized_title}')")
    slog(f"Songs in DB for artist_id={artist.id}:")
    for s in songs:
        print(f"    - '{s.title}' (normalized: '{normalize_title(s.title)}') | match: {normalize_title(s.title) == normalized_title}")

    song = next(
        (s for s in songs if normalize_title(s.title) == normalized_title),
        None
    )

    slog(f"Match found: {song}")

    if song:
        if mode == "skip":
            print(" -> Song exists, skipping.")
            return song, "skipped"

        elif mode == "update":
            song.year = year or song.year
            song.language = language
            song.album = album or song.album
            music_session.commit()
            print(" -> Song updated.")
            return song, "updated"

    else:
        song = Song(
            title=title,
            artist_id=artist.id,
            album=album,
            year=year,
            language=language,
            nostalgic=0,
            melancholic=0,
            party=0,
        )
        music_session.add(song)
        music_session.commit()
        print(" -> Added new song.")
        return song, "added"


def apply_tag(tag_session, song, file: Path, folder: Path):
    tag_name = str(file.parent.relative_to(folder))

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



def import_data_from_mp3_tags(folder_path: str, mode: str = "skip"):
    music_session, tag_session = setup_sessions()

    added_songs = []

    folder = Path(folder_path).resolve()
    if not folder.exists():
        print(f"Folder {folder_path} does not exist.")
        return

    mp3_files = list(folder.rglob("*.mp3"))
    print(f"Found {len(mp3_files)} mp3 files.")

    added_count = 0
    updated_count = 0

    for file in mp3_files:
        try:
            metadata = extract_mp3_metadata(file)
            artist = resolve_artist(music_session, metadata["artist_name"], metadata["origin"], metadata["title"])
            song, status = upsert_song(music_session, artist, metadata, mode)

            if status == "skipped":
                continue

            if status == "added":
                added_count += 1
                added_songs.append({"artist_name": metadata["artist_name"], "title": metadata["title"]})
            elif status == "updated":
                updated_count += 1

            apply_tag(tag_session, song, file, folder)

        except Exception as e:
            print(f"Error reading {file}: {e}")

    music_session.close()
    tag_session.close()
    print(f"\nDone! Added {added_count}, updated {updated_count}.")
    return added_songs


def resolve_artist(music_session, artist_name: str, origin: str | None = None, song_name: str = None) -> Artist:
    all_artists = music_session.query(Artist).all()
    matching_artists = [a for a in all_artists if a.name.lower() == artist_name.lower()]

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
        f"Multiple artists found for '{artist_name} - {song_name}'. Choose the correct one:",
        choices=choices
    ).ask()

    return selected_artist

