import sys
from pathlib import Path
from types import SimpleNamespace

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.youtube import yt_cache as c


def _fake_song(artist_name, title, synonyms=None, language=None, youtube_video_id=None):
    artist = SimpleNamespace(name=artist_name, synonyms=synonyms)
    return SimpleNamespace(artist=artist, title=title, language=language, youtube_video_id=youtube_video_id)


# Real case: "Taco Hemingway - Fuck Your List" is age-restricted and
# excluded from YouTube's own search results entirely — confirmed true even
# for an authenticated Data API request, not just anonymous yt-dlp scraping
# (see docs/agent-notes/youtube-search-matching.md). No amount of search
# tuning can find it, so `Song.youtube_video_id` is a manual, persistent
# override: once a human confirms the right video, init_cache() pre-fills
# the cache entry's `video_id` with it, and create_yt_playlist()'s existing
# "already have a video_id, skip search" check (manage_youtube_playlists.py)
# picks it up for free — no search is ever attempted for this song again,
# even across a cache clear/re-sync.
def test_init_cache_prefills_video_id_from_manual_override(monkeypatch):
    monkeypatch.setattr(c, "save_cache", lambda cache: None)

    songs = [_fake_song("Taco Hemingway", "Fuck Your List", youtube_video_id="bLOSjREikDc")]
    cache = c.init_cache("playlist123", "Test Playlist", songs)

    key = c.make_song_key("Taco Hemingway", "Fuck Your List")
    assert cache["songs"][key]["video_id"] == "bLOSjREikDc"


def test_init_cache_leaves_video_id_none_without_override(monkeypatch):
    monkeypatch.setattr(c, "save_cache", lambda cache: None)

    songs = [_fake_song("Some Artist", "Some Song")]
    cache = c.init_cache("playlist123", "Test Playlist", songs)

    key = c.make_song_key("Some Artist", "Some Song")
    assert cache["songs"][key]["video_id"] is None
