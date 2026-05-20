import json
from pathlib import Path
from datetime import datetime


CACHE_FILE = Path("yt_playlist_cache.json")


def make_song_key(artist: str, title: str) -> str:
    return f"{artist}|||{title}"


def load_cache():
    if CACHE_FILE.exists():
        with CACHE_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    return None


def save_cache(cache: dict):
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def init_cache(playlist_id: str, playlist_name: str, songs):
    cache = {
        "playlist_id": playlist_id,
        "playlist_name": playlist_name,
        "created_at": datetime.now().isoformat(),
        "songs": {}
    }

    for song in songs:
        artist = song.artist.name
        title = song.title
        key = make_song_key(artist, title)

        cache["songs"][key] = {
            "artist": artist,
            "title": title,
            "video_id": None,
            "added": False
        }

    save_cache(cache)
    return cache



def clear_cache():
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
