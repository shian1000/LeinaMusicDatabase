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


# The file holds every unfinished playlist by name (YT-08): starting a new one
# used to overwrite another's progress.

def test_two_unfinished_playlists_keep_their_own_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(c, "CACHE_FILE", tmp_path / "yt_playlist_cache.json")
    rock = c.init_cache("PL1", "Rock", [_fake_song("Queen", "Bicycle")])
    rock["songs"][c.make_song_key("Queen", "Bicycle")]["added"] = True
    c.save_cache(rock)
    c.init_cache("PL2", "Jazz", [_fake_song("Miles Davis", "So What")])

    assert sorted(c.unfinished_playlists()) == ["Jazz", "Rock"]
    assert c.load_cache("Rock")["songs"][c.make_song_key("Queen", "Bicycle")]["added"] is True
    assert c.load_cache("Jazz")["playlist_id"] == "PL2"
    assert c.load_cache("Pop") is None

    c.clear_cache("Rock")
    assert c.unfinished_playlists() == ["Jazz"]
    c.clear_cache("Jazz")
    assert not c.CACHE_FILE.exists()


def test_a_cache_file_in_the_old_single_playlist_format_is_still_read(monkeypatch, tmp_path):
    monkeypatch.setattr(c, "CACHE_FILE", tmp_path / "yt_playlist_cache.json")
    c.CACHE_FILE.write_text('{"playlist_id": "PL1", "playlist_name": "Rock", "songs": {}}', encoding="utf-8")
    assert c.load_cache("Rock")["playlist_id"] == "PL1"

    c.init_cache("PL2", "Jazz", [])
    assert sorted(c.unfinished_playlists()) == ["Jazz", "Rock"]

    c.CACHE_FILE.write_text("null", encoding="utf-8")  # left by a failed start
    assert c.load_cache("Rock") is None


def test_resuming_takes_the_songs_picked_now(monkeypatch, tmp_path):
    monkeypatch.setattr(c, "CACHE_FILE", tmp_path / "yt_playlist_cache.json")
    cache = c.init_cache("PL1", "Rock", [_fake_song("Queen", "Bicycle"), _fake_song("Queen", "Innuendo"),
                                         _fake_song("ABBA", "SOS")])
    cache["songs"][c.make_song_key("ABBA", "SOS")]["added"] = True

    new, dropped = c.update_cache_songs(cache, [_fake_song("Queen", "Bicycle"), _fake_song("Muse", "Uprising")])

    assert (new, dropped) == (1, 1)
    assert sorted(c.load_cache("Rock")["songs"]) == sorted(
        c.make_song_key(*s) for s in [("Queen", "Bicycle"), ("Muse", "Uprising"), ("ABBA", "SOS")])


def test_a_playlist_cached_under_titles_resumes_without_adding_songs_twice(monkeypatch, tmp_path):
    # YT-18: entries are keyed by song id now; one started before keeps its "added" marks
    monkeypatch.setattr(c, "CACHE_FILE", tmp_path / "yt_playlist_cache.json")
    old = c.init_cache("PL1", "Rock", [_fake_song("Queen", "Bicycle"), _fake_song("Muse", "Uprising")])
    old["songs"][c.make_song_key("Queen", "Bicycle")]["added"] = True
    c.save_cache(old)
    bicycle, uprising = _fake_song("Queen", "Bicycle"), _fake_song("Muse", "Uprising")
    bicycle.id, uprising.id = 7, 8

    cache = c.load_cache("Rock")
    assert c.update_cache_songs(cache, [bicycle, uprising]) == (0, 0)
    assert set(cache["songs"]) == {"7", "8"}
    assert cache["songs"]["7"]["added"] is True and cache["songs"]["8"]["added"] is False
