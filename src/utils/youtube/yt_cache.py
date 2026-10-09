import json
from pathlib import Path
from datetime import datetime

from utils.database.song_artists import song_artist_credits, song_main_artist_label


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
        title = song.title
        # Keyed on the primary artist alone - create_yt_playlist() maps keys
        # back to Song rows the same way
        key = make_song_key(song.artist.name, title)

        cache["songs"][key] = {
            # "A x B" for a multi-artist song - what YouTube gets queried with
            "artist": song_main_artist_label(song),
            "title": title,
            "synonyms": song.artist.synonyms,
            "language": song.language,
            # None for a single-artist song; see song_artist_credits()
            "credits": song_artist_credits(song),
            # A manually-confirmed video, set once and reused forever — for
            # songs no automated search will ever find (e.g. a video
            # YouTube's own search excludes from results for being
            # age-restricted, confirmed true even for an authenticated Data
            # API request — see docs/agent-notes/youtube-search-matching.md,
            # "Taco Hemingway - Fuck Your List"). Pre-filling it here means
            # create_yt_playlist()'s existing "already have a video_id, skip
            # search" check picks it up for free. Can also be
            # manage_youtube_playlists.NO_VIDEO_SENTINEL ("N/A") — a human's
            # record that no video for this song exists on YouTube at all,
            # currently treated identically to an unset field (normalized to
            # "no stored link", search still runs) since there's no
            # dedicated runtime behavior for it yet.
            "video_id": song.youtube_video_id,
            "added": False
        }

    save_cache(cache)
    return cache



def clear_cache():
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
