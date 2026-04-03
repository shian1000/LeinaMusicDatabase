from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from utils.database.datatables import Song
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # Goes from src → project root
DATABASE_URL = f"sqlite:///{BASE_DIR}/database/music.db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
session = SessionLocal()

def get_songs_by_language(language: str):
    songs = session.query(Song).filter(Song.language == language).all()
    if not songs:
        return []

    unique = set()
    song_list = []

    for song in songs:
        key = (song.title.lower(), song.artist.name.lower())
        if key not in unique:
            unique.add(key)
            song_list.append({
                "title": song.title,
                "artist": song.artist.name
            })

    return song_list



if __name__ == "__main__":
    print(get_songs_by_language("Polish"))
