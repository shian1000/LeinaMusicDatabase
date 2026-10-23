"""check_spelling() answers served from the disk cache
(utils/common/spellcheck_cache.py): its key ignores case, so a hit must be
given back in the spelling of the current input. A temp cache file, the
MusicBrainz request mocked."""
import pytest

from utils.common import spellcheck_cache, text_utils


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(spellcheck_cache, "_CACHE_PATH", tmp_path / "spellcheck_cache.json")
    monkeypatch.setattr(spellcheck_cache, "_cache", None)
    monkeypatch.setattr(spellcheck_cache, "_dirty", False)
    monkeypatch.setattr(spellcheck_cache, "_pending", 0)


def _musicbrainz(monkeypatch, recordings):
    class Response:
        def json(self):
            return {"recordings": recordings}
    calls = []
    monkeypatch.setattr(text_utils, "mb_get", lambda *a, **k: calls.append(a) or Response())
    return calls


def test_a_hit_for_another_spelling_is_no_correction(cache, monkeypatch):
    # COM-05: "Abba" found nothing; asking "ABBA" next got corrected_artist
    # "Abba", which the caller took for a correction
    calls = _musicbrainz(monkeypatch, [])
    first = text_utils.check_spelling("Abba", "waterloo")
    asked = len(calls)
    second = text_utils.check_spelling("ABBA", "Waterloo")

    assert len(calls) == asked  # served from the cache
    assert (first["corrected_artist"], first["corrected_title"]) == ("Abba", "waterloo")
    assert (second["input_artist"], second["corrected_artist"]) == ("ABBA", "ABBA")
    assert (second["input_title"], second["corrected_title"]) == ("Waterloo", "Waterloo")
    assert not second["corrected"] and not second["found"]


def test_a_hit_keeps_a_real_correction(cache, monkeypatch):
    _musicbrainz(monkeypatch, [{"title": "Waterloo", "score": 100,
                                "artist-credit": [{"artist": {"name": "ABBA"}}]}])
    text_utils.check_spelling("Abbba", "Waterloo")
    _musicbrainz(monkeypatch, [])

    again = text_utils.check_spelling("ABBBA", "waterloo")

    assert again["found"] and again["corrected"]
    assert (again["corrected_artist"], again["corrected_title"]) == ("ABBA", "Waterloo")
    assert (again["input_artist"], again["input_title"]) == ("ABBBA", "waterloo")
