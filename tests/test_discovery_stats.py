import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from upath import UPath

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.discoveries import discovery_stats
from utils.discoveries import discoveries_manager


@pytest.fixture(autouse=True)
def isolated_stats_file(tmp_path, monkeypatch):
    """Point discovery_stats at a throwaway file and reset its in-memory
    cache, so tests never touch the real discovery_fetcher_stats.json or leak
    counters between each other."""
    stats_file = tmp_path / "discovery_fetcher_stats.json"
    monkeypatch.setattr(discovery_stats, "_stats_path", lambda: UPath(str(stats_file)))
    discovery_stats._cache = None
    yield
    discovery_stats._cache = None


def test_load_discovery_stats_missing_file_returns_empty_dict():
    assert discovery_stats.load_discovery_stats() == {}


def test_record_invocation_creates_entry_and_persists():
    discovery_stats.record_invocation("spotify_fetcher")

    assert discovery_stats.load_discovery_stats() == {
        "spotify_fetcher": {"invocations": 1, "successes": 0}
    }


def test_record_success_increments_successes_without_double_counting_invocations():
    discovery_stats.record_invocation("spotify_fetcher")
    discovery_stats.record_success("spotify_fetcher")

    assert discovery_stats.load_discovery_stats() == {
        "spotify_fetcher": {"invocations": 1, "successes": 1}
    }


def test_counters_accumulate_across_multiple_calls_and_modules():
    discovery_stats.record_invocation("spotify_fetcher")
    discovery_stats.record_invocation("spotify_fetcher")
    discovery_stats.record_success("spotify_fetcher")
    discovery_stats.record_invocation("wikipedia_fetcher")

    stats = discovery_stats.load_discovery_stats()
    assert stats["spotify_fetcher"] == {"invocations": 2, "successes": 1}
    assert stats["wikipedia_fetcher"] == {"invocations": 1, "successes": 0}


def test_stats_survive_the_in_memory_cache_being_dropped():
    discovery_stats.record_invocation("spotify_fetcher")
    discovery_stats._cache = None  # simulate a fresh process picking the file back up

    discovery_stats.record_success("spotify_fetcher")

    assert discovery_stats.load_discovery_stats() == {
        "spotify_fetcher": {"invocations": 1, "successes": 1}
    }


def _fake_song():
    artist = SimpleNamespace(name="Some Artist", synonyms=None)
    return SimpleNamespace(artist=artist, title="Some Title", album=None)


def test_call_module_records_invocation_but_not_success_when_result_is_invalid():
    module = SimpleNamespace(get_album_name=lambda artist, title: None)

    album = discoveries_manager._call_module(module, "fake_fetcher", "Fake Fetcher", "Some Artist", "Some Title")

    assert album is None
    assert discovery_stats.load_discovery_stats() == {
        "fake_fetcher": {"invocations": 1, "successes": 0}
    }


def test_call_module_records_success_when_result_validates():
    module = SimpleNamespace(get_album_name=lambda artist, title: "Fantastic Voyage")

    album = discoveries_manager._call_module(module, "fake_fetcher", "Fake Fetcher", "Some Artist", "Some Title")

    assert album == "Fantastic Voyage"
    assert discovery_stats.load_discovery_stats() == {
        "fake_fetcher": {"invocations": 1, "successes": 1}
    }


def test_call_module_records_invocation_but_not_success_when_module_crashes():
    def boom(artist, title):
        raise RuntimeError("network error")

    module = SimpleNamespace(get_album_name=boom)

    album = discoveries_manager._call_module(module, "fake_fetcher", "Fake Fetcher", "Some Artist", "Some Title")

    assert album is None
    assert discovery_stats.load_discovery_stats() == {
        "fake_fetcher": {"invocations": 1, "successes": 0}
    }


def test_discover_album_name_records_one_invocation_per_module_tried():
    song = _fake_song()
    modules = [
        ("first_fetcher", "First Fetcher", SimpleNamespace(get_album_name=lambda artist, title: None)),
        ("second_fetcher", "Second Fetcher", SimpleNamespace(get_album_name=lambda artist, title: "Found It")),
    ]

    album = discoveries_manager.discover_album_name(song, modules)

    assert album == "Found It"
    stats = discovery_stats.load_discovery_stats()
    assert stats["first_fetcher"]["invocations"] == 1
    assert stats["first_fetcher"]["successes"] == 0
    assert stats["second_fetcher"]["invocations"] == 1
    assert stats["second_fetcher"]["successes"] == 1
