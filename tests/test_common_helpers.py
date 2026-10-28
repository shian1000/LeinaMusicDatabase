"""Shared helpers no test covered before (COM-12): song tags
(utils/database/tags_management.py, in-memory tag.db), the writing-system
helpers (utils/common/script_utils.py) and the MusicBrainz client's rate
limit and retries (utils/common/musicbrainz_client.py, clock and HTTP
faked - nothing waits, nothing goes out)."""
from types import SimpleNamespace

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from utils.common import musicbrainz_client as mb
from utils.common import script_utils as su
from utils.database import tags_management as tm
from utils.database.tag_db_manager import Base, SongTag, Tag


# ==================== tags ====================

@pytest.fixture
def tags(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(tm, "get_global_database_sessions", lambda: (None, session))
    yield session
    session.close()


def _song(song_id):
    return SimpleNamespace(id=song_id)


def test_a_tag_is_created_once_and_linked_once(tags):
    assert tm.add_tag_to_song(_song(1), " workout ")
    assert tm.add_tag_to_song(_song(1), "workout")
    assert tm.add_tag_to_song(_song(2), "workout")
    assert [t.name for t in tags.query(Tag)] == ["workout"]
    assert sorted(st.song_id for st in tags.query(SongTag)) == [1, 2]


def test_removing_a_tag_keeps_the_others(tags):
    for name in ("workout", "chill"):
        tm.add_tag_to_song(_song(1), name)
    assert tm.remove_tag_from_song(_song(1), "workout")
    assert tm.remove_tag_from_song(_song(1), "workout")       # already gone: still fine
    assert tm.remove_tag_from_song(_song(1), "never-existed")
    assert not tm.has_tag_on_song(_song(1), "workout")
    assert tm.has_tag_on_song(_song(1), "chill")


@pytest.mark.parametrize("song, tag", [
    (SimpleNamespace(), "x"), (_song(None), "x"), (_song(1), ""), (_song(1), "   "), (_song(1), None),
])
def test_an_invalid_song_or_tag_changes_nothing(tags, song, tag):
    assert not tm.add_tag_to_song(song, tag)
    assert not tm.has_tag_on_song(song, tag)
    assert not tm.remove_tag_from_song(song, tag)
    assert tags.query(Tag).count() == 0


def test_without_the_menus_sessions_nothing_is_tagged(monkeypatch):
    monkeypatch.setattr(tm, "get_global_database_sessions", lambda: None)
    assert not tm.add_tag_to_song(_song(1), "workout")
    assert tm.song_ids_with_tag("workout") == set()
    assert tm.tag_names_by_song([1]) == {}


def test_songs_with_a_tag_and_tags_by_song_in_one_query(tags):
    tm.add_tag_to_song(_song(1), "workout")
    tm.add_tag_to_song(_song(1), "chill")
    tm.add_tag_to_song(_song(2), "workout")
    tm.add_tag_to_song(_song(3), "chill")

    assert tm.song_ids_with_tag(" workout ") == {1, 2}
    assert tm.song_ids_with_tag("") == set()
    assert tm.tag_names_by_song([1, 2, 99]) == {1: ["chill", "workout"], 2: ["workout"]}


# ==================== scripts ====================

@pytest.mark.parametrize("text, scripts", [
    ("Björk", {"LATIN"}),
    ("Там, де ти є", {"CYRILLIC"}),
    ("Вjörk", {"LATIN", "CYRILLIC"}),
    ("방탄소년단", {"HANGUL"}),
    ("カタカナ ひらがな", {"KANA"}),
    ("123 - !", set()),
    (None, set()),
])
def test_scripts_in(text, scripts):
    assert su.scripts_in(text) == scripts
    assert su.has_non_latin(text) == bool(scripts - {"LATIN"})


@pytest.mark.parametrize("a, b", [
    ("Neformat", "Неформат"),
    ("Sakartvelo", "საქართველო"),
    ("Hangul", "한굴"),
])
def test_matching_key_brings_both_scripts_together(a, b):
    assert su.matching_key(a) == su.matching_key(b)


def test_matching_key_is_near_equal_across_transliteration_schemes():
    # "ye" for "є" in one scheme, "e" in the table: close, not equal - it feeds a similarity check
    from utils.common.text_utils import similarity
    a, b = su.matching_key("Tam, de ty ye"), su.matching_key("Там, де ти є")
    assert a != b and similarity(a, b) >= 0.9
    assert similarity(su.matching_key("Tam, de ty ye"), su.matching_key("Dzień dobry")) < 0.5


def test_matching_key_of_nothing_is_empty():
    assert su.matching_key(None) == su.matching_key("") == ""


def test_hangul_is_romanized_the_same_in_both_copies():
    # COM-47: _hangul() is copied in romanize.py - the two must not drift apart
    from utils.common import romanize
    for ch in "가나다한글방탄소년단":
        assert su._hangul(ch) == romanize._hangul(ch)


@pytest.mark.parametrize("query, context, expected", [
    ("Neformat", ["Восени"], "Неформат"),
    ("Yolka", ["Ёлка"], "Ёлка"),
    ("Neformat", ["Autumn"], None),          # nothing around it in Cyrillic
    ("Неформат", ["Восени"], None),          # not a Latin query
    ("", ["Восени"], None),
])
def test_cyrillic_search_variant(query, context, expected):
    assert su.cyrillic_search_variant(query, context) == expected


def test_latin_to_cyrillic_keeps_case_and_reads_y_by_its_neighbour():
    assert su.latin_to_cyrillic("Shchedryk") == "Щедрык"
    assert su.latin_to_cyrillic("Mayak") == "Маяк"
    assert su.latin_to_cyrillic("Kyiv") == "Кыив"  # y after a consonant: ы
    assert su.latin_to_cyrillic("Ty 2") == "Ты 2"


@pytest.mark.parametrize("text, fixed", [
    ("Вjörk", "Björk"),                       # Cyrillic В in a Latin word
    ("Рiдна мати", "Рідна мати"),             # Latin i in a Cyrillic word
    ("COVERКАВЕР", "COVERКАВЕР"),             # a tie: two words run together, left alone
    ("ИΔΤVИ", "ИΔΤVИ"),                      # a third script: a stylized name, left alone
    ("Björk", "Björk"),
    (None, None),
])
def test_fix_mixed_script_words(text, fixed):
    assert su.fix_mixed_script_words(text) == fixed


# ==================== MusicBrainz client ====================

class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


@pytest.fixture
def client(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(mb, "time", clock)
    monkeypatch.setattr(mb, "_last_request_ts", 0.0)
    monkeypatch.setattr(mb, "slog", lambda *a, **k: None)
    mb.MBStats.reset()
    answers = []
    sent = []

    def get(url, params, timeout):
        sent.append((url, params, timeout))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        response = SimpleNamespace(status_code=answer)

        def raise_for_status():
            if answer >= 400:
                raise requests.exceptions.HTTPError(response=response)
        response.raise_for_status = raise_for_status
        return response
    monkeypatch.setattr(mb._session, "get", get)
    yield SimpleNamespace(clock=clock, answers=answers, sent=sent)
    mb.MBStats.reset()


def test_requests_are_spaced_by_the_minimum_interval(client):
    client.answers += [200, 200]
    assert mb.mb_get("u", {}).status_code == 200
    client.clock.now += 0.3
    assert mb.mb_get("u", {}).status_code == 200
    assert client.clock.slept == [round(mb.MUSICBRAINZ_API_MIN_INTERVAL - 0.3, 3)]
    assert all(timeout == mb.MUSICBRAINZ_API_TIMEOUT for _, _, timeout in client.sent)


def test_503_and_429_are_retried_with_a_growing_pause(client):
    client.answers += [503, 429, 200]
    assert mb.mb_get("u", {}, retries=3, backoff=5.0).status_code == 200
    assert 5.0 in client.clock.slept and 10.0 in client.clock.slept
    assert (mb.MBStats.http_503, mb.MBStats.http_429, mb.MBStats.requests_made) == (1, 1, 3)


def test_other_http_errors_and_used_up_retries_give_none(client):
    client.answers += [404]
    assert mb.mb_get("u", {}) is None
    assert len(client.sent) == 1                       # a 404 isn't retried
    client.answers += [503, 503]
    assert mb.mb_get("u", {}, retries=2) is None
    assert len(client.sent) == 3


def test_a_network_error_is_retried_after_a_short_pause(client):
    client.answers += [requests.exceptions.ConnectionError("reset"), 200]
    assert mb.mb_get("u", {}).status_code == 200
    assert 2.0 in client.clock.slept
    assert mb.MBStats.other_errors == 1


def test_retries_1_is_a_single_try(client):
    # COM-40: "retries" counts every try, not the ones after the first
    client.answers += [requests.exceptions.Timeout("slow")]
    assert mb.mb_get("u", {}, retries=1) is None
    assert len(client.sent) == 1


@pytest.mark.parametrize("text, quoted", [
    ('12" Mix', '"12\\" Mix"'),
    ("a\\b", '"a\\\\b"'),
    (None, '""'),
])
def test_lucene_quote(text, quoted):
    assert mb.lucene_quote(text) == quoted
