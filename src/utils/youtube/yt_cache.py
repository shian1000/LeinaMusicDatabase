import json
from pathlib import Path
from datetime import datetime

from utils.database.song_artists import song_artist_credits, song_main_artist_label
from utils.database.latin_names import artist_match_synonyms
from config.constants import PROJECT_ROOT, SONG_KIND_FULL_ALBUM


CACHE_FILE = PROJECT_ROOT / "yt_playlist_cache.json"


def make_song_key(artist: str, title: str) -> str:
    return f"{artist}|||{title}"


def _read_all() -> dict:
    """Every unfinished playlist on disk, by name. The file used to hold a
    single playlist's cache at its top level (and "null" after a failed
    start), which reads as that one playlist / none."""
    if not CACHE_FILE.exists():
        return {}
    with CACHE_FILE.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {}
    if "playlists" in data:
        return data["playlists"]
    return {data["playlist_name"]: data} if data.get("playlist_name") else {}


def _write_all(playlists: dict):
    if not playlists:
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()
        return
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump({"playlists": playlists}, f, ensure_ascii=False, indent=2)


def load_cache(playlist_name: str):
    """The unfinished playlist of this name, or None."""
    return _read_all().get(playlist_name)


def unfinished_playlists() -> list[str]:
    return list(_read_all())


def save_cache(cache: dict):
    """Saves this playlist's progress; other unfinished playlists are kept."""
    playlists = _read_all()
    playlists[cache["playlist_name"]] = cache
    _write_all(playlists)


def init_cache(playlist_id: str, playlist_name: str, songs):
    cache = {
        "playlist_id": playlist_id,
        "playlist_name": playlist_name,
        "created_at": datetime.now().isoformat(),
        "songs": {}
    }

    for song in songs:
        # Keyed on the primary artist alone - create_yt_playlist() maps keys
        # back to Song rows the same way
        cache["songs"][make_song_key(song.artist.name, song.title)] = _song_entry(song)

    save_cache(cache)
    return cache


def update_cache_songs(cache: dict, songs) -> tuple[int, int]:
    """Resuming with the songs picked now: a song not in the cache yet is
    added, one never added to the playlist that isn't among them is dropped
    (added ones stay, as a record). Returns (new, dropped) and saves."""
    wanted = {make_song_key(song.artist.name, song.title): song for song in songs}
    dropped = [key for key, entry in cache["songs"].items() if key not in wanted and not entry["added"]]
    for key in dropped:
        del cache["songs"][key]
    new = [key for key in wanted if key not in cache["songs"]]
    for key in new:
        cache["songs"][key] = _song_entry(wanted[key])
    save_cache(cache)
    return len(new), len(dropped)


def _song_entry(song) -> dict:
    title = song.title
    return {
        # "A x B" for a multi-artist song - what YouTube gets queried with
        "artist": song_main_artist_label(song),
        "title": title,
        "title_latin": getattr(song, "title_latin", None),  # retried after the title, see search_video()
        "full_album": getattr(song, "kind", None) == SONG_KIND_FULL_ALBUM,  # songs.kind, see search_video()
        "synonyms": artist_match_synonyms(song.artist),  # Latin form included
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


def clear_cache(playlist_name: str):
    """Forgets this playlist's progress (finished or started over)."""
    playlists = _read_all()
    if playlists.pop(playlist_name, None) is not None:
        _write_all(playlists)
