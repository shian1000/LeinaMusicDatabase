"""check_spelling() answers served from the disk cache
(utils/common/spellcheck_cache.py): its key ignores case, so a hit must be
given back in the spelling of the current input. A temp cache file, the
MusicBrainz request mocked."""
import json

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


def test_answers_not_yet_saved_are_written_when_the_app_exits(tmp_path):
    # COM-24: only the import saved; Check spelling lost up to 19 answers on exit
    import subprocess
    import sys
    from pathlib import Path
    tests = Path(__file__).resolve().parent
    target = tmp_path / "spellcheck_cache.json"
    code = (f"import sys; sys.path.insert(0, {str(tests)!r}); import conftest; "
            "from pathlib import Path; from utils.common import spellcheck_cache as c; "
            f"c._CACHE_PATH = Path({str(target)!r}); c.put('Abba', 'Waterloo', {{'found': True}}); "
            "conftest.pytest_unconfigure(None)")
    subprocess.run([sys.executable, "-c", code], cwd=tests.parent / "src", check=True)
    assert len(json.loads(target.read_text())) == 1


def test_a_failed_save_is_logged_and_leaves_no_temp_file(cache, tmp_path, monkeypatch):
    logged = []
    monkeypatch.setattr(spellcheck_cache, "flog", logged.append)
    spellcheck_cache.put("Abba", "Waterloo", {"found": True})
    monkeypatch.setattr(spellcheck_cache.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    spellcheck_cache.save()
    assert logged and "disk full" in logged[0]
    assert list(tmp_path.iterdir()) == []
