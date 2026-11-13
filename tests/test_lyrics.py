"""Songs' lyrics: lyrics.db (utils/database/lyrics_store.py - saving, what
each module didn't find and when it's asked again, instrumental songs),
discoveries_manager.discover_lyrics()'s rules (title and artist; length only
for keeping the synced lines; a failing module apart from one finding
nothing), the LRCLIB, Genius and library file modules, the review lists of
Fill missing data -> Lyrics, replaying
merges / deletions into lyrics.db, the sharing server's answer for one song,
and lyrics.db's tables never reaching the public copy. Temp-file SQLite; the
web is faked."""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from mutagen.id3 import ID3, USLT
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config.constants import LYRICS_MISS_REJECTED, LYRICS_RETRY_DAYS, SONG_KIND_FULL_ALBUM, VERSION_INSTRUMENTAL
from musicdatabase_core import song_id_replay
from utils.database import lyrics_store as store
from utils.database import public_export, sharing_server
from utils.database.datatables import Artist, Base, Song
from utils.discoveries import discoveries_manager as dm
from utils.discoveries.discovery_modules import (genius_lyrics_fetcher, library_tags_fetcher, lrclib_fetcher,
                                                 cuspajz_fetcher, fajnanuta_fetcher, lyricstranslate_fetcher,
                                                 musixmatch_fetcher, tekstowo_fetcher)
from utils.discoveries.discovery_result import LyricsDiscoveryResult
from utils.discoveries import lyrics_pages
from utils.ui import lyrics_review

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def lyrics_db(tmp_path):
    return store.ensure_lyrics_db(tmp_path / "lyrics.db")


@pytest.fixture
def music(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _song(title="Kometa", artist="Lao Che", song_id=None, **fields):
    return Song(id=song_id, title=title, artist=Artist(name=artist), **fields)


# ---- lyrics.db ----

def test_saved_lyrics_come_back(lyrics_db):
    with store.lyrics_db_session(lyrics_db) as session:
        store.save_lyrics(session, 7, store.Lyrics(plain="Raz\nDwa", synced="[00:01.00] Raz", source="lrclib_fetcher"))
        session.commit()
        assert store.stored_lyrics(session, 7) == store.Lyrics("Raz\nDwa", "[00:01.00] Raz", False, "lrclib_fetcher")


def test_windows_line_ends_are_stored_as_plain_new_lines(lyrics_db):
    with store.lyrics_db_session(lyrics_db) as session:
        store.save_lyrics(session, 7, store.Lyrics(plain="Raz\r\nDwa\rTrzy", synced="[00:01.00] Raz\r\n", source="m"))
        session.commit()
        assert store.stored_lyrics(session, 7) == store.Lyrics("Raz\nDwa\nTrzy", "[00:01.00] Raz\n", False, "m")


def test_a_module_that_found_nothing_is_asked_again_after_a_while_a_rejected_one_never(lyrics_db):
    with store.lyrics_db_session(lyrics_db) as session:
        store.record_miss(session, _song(song_id=1), "lrclib_fetcher")
        store.save_lyrics(session, 3, store.Lyrics(plain="x", source="genius_lyrics_fetcher"))
        store.reject_lyrics(session, _song(song_id=3))
        session.commit()
        later = datetime.now(timezone.utc) + timedelta(days=LYRICS_RETRY_DAYS + 1)
        assert store.answered_modules(session) == {1: {"lrclib_fetcher"}, 3: {"genius_lyrics_fetcher"}}
        assert store.answered_modules(session, now=later) == {3: {"genius_lyrics_fetcher"}}
        assert store.stored_lyrics(session, 3) is None  # rejecting drops the text


def test_only_the_modules_that_havent_answered_are_asked(music, lyrics_db):
    plain, done, album = _song("Plain"), _song("Done"), _song("Whole", kind=SONG_KIND_FULL_ALBUM)
    asked, wordless, instrumental_take = _song("Asked"), _song("Mood", language="Instrumental"), \
        _song("Plain", version_kind=VERSION_INSTRUMENTAL)
    music.add_all([plain, done, album, asked, wordless, instrumental_take])
    music.commit()
    with store.lyrics_db_session(lyrics_db) as session:
        store.save_lyrics(session, done.id, store.Lyrics(plain="x", source="m"))
        store.record_miss(session, asked, "lrclib_fetcher")
        store.record_miss(session, plain, "lrclib_fetcher")
        store.record_miss(session, plain, "genius_lyrics_fetcher")
        session.commit()
        assert store.songs_missing_lyrics(music, session, ["lrclib_fetcher", "genius_lyrics_fetcher"]) == \
            [(asked, ["genius_lyrics_fetcher"])]
        # A new module asks every song once
        assert [song for song, _ in store.songs_missing_lyrics(music, session, ["new_fetcher"])] == [asked, plain]


def test_a_song_renamed_since_is_asked_again_and_forgetting_asks_every_module(music, lyrics_db):
    song = _song("Arab - Moon (prod. Eazy Dyonizy)", "ARAB TV")
    music.add(song)
    music.commit()
    with store.lyrics_db_session(lyrics_db) as session:
        store.record_miss(session, song, "lrclib_fetcher")
        session.commit()
        assert store.songs_missing_lyrics(music, session, ["lrclib_fetcher"]) == []
        song.title = "Moon"
        assert store.songs_missing_lyrics(music, session, ["lrclib_fetcher"]) == [(song, ["lrclib_fetcher"])]
        song.title = "Arab - Moon (prod. Eazy Dyonizy)"
        store.save_lyrics(session, song.id, store.Lyrics(plain="x", source="m"))
        store.forget_lookups(session, song.id)
        session.commit()
        assert store.songs_missing_lyrics(music, session, ["lrclib_fetcher"]) == [(song, ["lrclib_fetcher"])]


def test_marking_songs_instrumental():
    song = _song()
    store.mark_instrumental([song])
    assert song.language == "Instrumental" and store.is_instrumental(song)


def test_a_version_gets_its_originals_plain_text(music, lyrics_db):
    original = _song("Kometa")
    music.add(original)
    music.commit()
    live = _song("Kometa", version_kind="live", version_of=original.id)
    with store.lyrics_db_session(lyrics_db) as session:
        assert store.original_lyrics(session, live) is None
        store.save_lyrics(session, original.id, store.Lyrics(plain="Słowa", synced="[00:01.00] Słowa", source="m"))
        found = store.original_lyrics(session, live)
    assert (found.plain, found.synced, found.source) == ("Słowa", None, "original")


# ---- matching ----

def _module(*candidates, calls=None):
    class Module:
        @staticmethod
        def get_lyrics(artist, title, duration_s=None, songs=None):
            if calls is not None:
                calls.append(artist)
            return list(candidates)
    return [("fake_fetcher", "Fake", Module)]


def _found(title="Kometa", artist="Lao Che", duration=200.0, synced="[00:01.00] a"):
    return LyricsDiscoveryResult(plain="a", synced=synced, matched_title=title, matched_artist=artist,
                                 duration_s=duration)


def test_the_same_title_and_artist_is_sure_and_the_length_decides_the_synced_lines():
    near = dm.discover_lyrics(_song(), _module(_found(duration=201.5)), duration_s=200).match
    video = dm.discover_lyrics(_song(), _module(_found(duration=266)), duration_s=326).match  # a video's intro
    unknown = dm.discover_lyrics(_song(), _module(_found()), duration_s=None).match
    assert near.sure and near.synced_fits and near.module_id == "fake_fetcher"
    assert video.sure and not video.synced_fits
    assert unknown.sure and unknown.synced_fits
    assert lyrics_review._as_lyrics(video).synced is None and lyrics_review._as_lyrics(near).synced


def test_a_resembling_title_is_for_review():
    match = dm.discover_lyrics(_song(), _module(_found(title="Kometta")), duration_s=200).match
    assert match is not None and not match.sure
    # A bracket is a note, not other words
    assert dm.discover_lyrics(_song(), _module(_found(title="Kometa (Remastered)")), duration_s=200).match.sure


@pytest.mark.parametrize("found", [_found(title="Hydropiekłowstąpienie"), _found(artist="Kult"),
                                   LyricsDiscoveryResult(matched_title="Kometa", matched_artist="Lao Che")])
def test_another_song_artist_or_an_empty_text_is_left_out_and_the_module_missed(found):
    search = dm.discover_lyrics(_song(), _module(found), duration_s=200)
    assert search.match is None and search.missed == ["fake_fetcher"] and search.failed == []


def test_a_failing_module_is_not_a_miss_and_the_next_one_is_asked():
    class Failing:
        @staticmethod
        def get_lyrics(artist, title, duration_s=None, songs=None):
            raise LookupError("429")

    search = dm.discover_lyrics(_song(), [("lrclib_fetcher", "LRCLIB", Failing), *_module(_found())], 200)
    assert search.failed == ["lrclib_fetcher"] and search.missed == [] and search.match.sure


def test_the_closest_length_wins_and_other_titles_count():
    song = _song("Na jednej z dzikich plaż", other_titles="Lubiła tańczyć")
    match = dm.discover_lyrics(song, _module(_found("Lubiła tańczyć", duration=190, synced=None),
                                             _found("Lubiła tańczyć", duration=200.4)), duration_s=200).match
    assert match.sure and match.found_duration_s == 200.4


def test_a_title_with_a_credit_in_brackets_is_asked_and_compared_without_it():
    asked = []

    class Module:
        @staticmethod
        def get_lyrics(artist, title, duration_s=None, songs=None):
            asked.append(title)
            return [] if "(" in title else [_found("Armani Top", "3LNA")]

    match = dm.discover_lyrics(_song("ARMANI TOP (prod.bovskey)", "3LNA"), [("m", "M", Module)], 160).match
    assert match.sure and asked == ["ARMANI TOP (prod.bovskey)", "ARMANI TOP"]


def test_the_queries_cover_joined_names_other_titles_and_the_covered_original():
    song = _song("Studzianki", "Alina Gingertail & Someone", other_titles="W moim ogródecku")
    song.covered_artist = Artist(name="Pixies")
    assert dm._lyrics_queries(song) == [
        ("Alina Gingertail & Someone", "Studzianki"), ("Alina Gingertail", "Studzianki"), ("Someone", "Studzianki"),
        ("Alina Gingertail & Someone", "W moim ogródecku"), ("Alina Gingertail", "W moim ogródecku"),
        ("Someone", "W moim ogródecku"), ("Pixies", "Studzianki")]


def test_a_duet_is_asked_as_the_sites_write_it_and_a_medley_song_by_song():
    from utils.database.datatables import AdditionalSongArtist

    duet = _song("Patrz I Słuchaj", "Anna Maria Jopek")
    duet.additional_artist_links = [AdditionalSongArtist(artist=Artist(name="Branford Marsalis"), role="main")]
    assert ("Anna Maria Jopek & Branford Marsalis", "Patrz I Słuchaj") in dm._lyrics_queries(duet)
    medley = _song("Steel for Humans / Lazare", "Alina Gingertail")
    assert dm._lyrics_queries(medley)[-2:] == [("Alina Gingertail", "Steel for Humans"), ("Alina Gingertail", "Lazare")]


def test_one_song_of_a_medley_is_for_review():
    match = dm.discover_lyrics(_song("Steel for Humans / Lazare", "Alina Gingertail"),
                               _module(_found("Steel for Humans", "Alina Gingertail")), 200).match
    assert match is not None and not match.sure


def test_a_covers_lyrics_come_from_the_original():
    class Module:
        @staticmethod
        def get_lyrics(artist, title, duration_s=None, songs=None):
            return [_found("Where Is My Mind?", "Pixies", duration=229)] if artist == "Pixies" else []

    song = _song("Where is my Mind", "Alicia Widar")
    song.covered_artist = Artist(name="Pixies")
    match = dm.discover_lyrics(song, [("m", "M", Module)], 190).match
    assert match.sure and not match.synced_fits  # another recording: the words, not the times


def test_a_synonym_is_asked_when_the_name_finds_nothing():
    calls = []
    song = _song(artist="Taras Chubai")
    song.artist.synonyms = "Тарас Чубай"
    assert dm.discover_lyrics(song, _module(calls=calls), duration_s=200).match is None
    assert calls == ["Taras Chubai", "Тарас Чубай"]


def test_a_text_from_the_songs_own_file_needs_no_checking():
    match = dm.discover_lyrics(_song(), _module(LyricsDiscoveryResult(plain="a", from_song_file=True))).match
    assert match.sure


# ---- modules ----

def test_lrclib_hands_every_result_on(monkeypatch):
    class Answer:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return [{"id": 9, "trackName": "Kometa", "artistName": "Lao Che", "duration": 201,
                     "plainLyrics": "a", "syncedLyrics": "[00:01.00] a", "instrumental": False}, "junk"]

    monkeypatch.setattr(lrclib_fetcher._pacer, "min_interval", 0)
    monkeypatch.setattr(lrclib_fetcher.requests, "get", lambda *a, **k: Answer())
    [found] = lrclib_fetcher.get_lyrics("Lao Che", "Kometa")
    assert (found.matched_title, found.duration_s, found.source_ref, found.synced) == \
           ("Kometa", 201.0, "9", "[00:01.00] a")


def test_lrclib_failing_raises(monkeypatch):
    def fail(*a, **k):
        raise lrclib_fetcher.requests.ConnectionError("offline")

    monkeypatch.setattr(lrclib_fetcher._pacer, "min_interval", 0)
    monkeypatch.setattr(lrclib_fetcher.requests, "get", fail)
    with pytest.raises(LookupError):
        lrclib_fetcher.get_lyrics("Lao Che", "Kometa")


def test_lrclib_waits_out_a_429(monkeypatch):
    answers = []

    class Answer:
        def __init__(self, status):
            self.status_code, self.headers = status, {"Retry-After": "2"}

        def raise_for_status(self):
            if self.status_code != 200:
                raise lrclib_fetcher.requests.HTTPError(str(self.status_code))

        def json(self):
            return []

    statuses = iter([429, 200])
    monkeypatch.setattr(lrclib_fetcher._pacer, "min_interval", 0)
    monkeypatch.setattr(lrclib_fetcher.time, "sleep", answers.append)
    monkeypatch.setattr(lrclib_fetcher.requests, "get", lambda *a, **k: Answer(next(statuses)))
    assert lrclib_fetcher.get_lyrics("Lao Che", "Kometa") == [] and answers == [2]


GENIUS_PAGE = """<html><div data-lyrics-container="true"><div data-exclude-from-selection="true">42 Contributors
Morrow Lyrics</div>[Verse 1]<br/>An early mornin'<br/>She's paranoid<br/><br/>[Chorus]<br/>I know</div></html>"""


def test_genius_reads_the_lyrics_page_of_hits_resembling_the_song(monkeypatch):
    hits = [{"type": "song", "result": {"id": 1, "title": "Гасі (Hasi)", "primary_artist": {"name": "AKUTE"},
                                        "url": "https://genius.com/a", "instrumental": False}},
            {"type": "song", "result": {"id": 2, "title": "Something else", "primary_artist": {"name": "AKUTE"},
                                        "url": "https://genius.com/b"}},
            {"type": "song", "result": {"id": 3, "title": "Гасі", "primary_artist": {"name": "AKUTE"},
                                        "url": "https://genius.com/c", "instrumental": True}}]
    pages = []

    class Answer:
        def __init__(self, url):
            self.url = url
            self.text = GENIUS_PAGE

        def json(self):
            return {"response": {"sections": [{"hits": hits}]}}

    def get(url, **params):
        pages.append(url)
        return Answer(url)

    monkeypatch.setattr(genius_lyrics_fetcher, "_get", get)
    first, instrumental = genius_lyrics_fetcher.get_lyrics("AKUTE", "Гасі")
    assert (first.matched_title, first.plain) == ("Гасі", "An early mornin'\nShe's paranoid\n\nI know")
    assert instrumental.instrumental and instrumental.plain is None
    assert pages == [genius_lyrics_fetcher.GENIUS_SITE_SEARCH_URL, "https://genius.com/a"]


def test_genius_asks_again_with_the_artists_country_when_a_name_is_shared(monkeypatch):
    asked = []

    class Answer:
        def __init__(self, query):
            self.query = query
            self.text = GENIUS_PAGE

        def json(self):
            hits = ([{"type": "song", "result": {"id": 9, "title": "Moon", "primary_artist": {"name": "Arab (POL)"},
                                                 "url": "https://genius.com/Arab-pol-moon-lyrics"}}]
                    if "(POL)" in self.query else
                    [{"type": "song", "result": {"id": 1, "title": "Arab Money", "primary_artist": {"name": "Busta Rhymes"},
                                                 "url": "https://genius.com/x"}}])
            return {"response": {"sections": [{"hits": hits}]}}

    def get(url, **params):
        asked.append(params.get("q", url))
        return Answer(params.get("q", ""))

    monkeypatch.setattr(genius_lyrics_fetcher, "_get", get)
    song = _song("Moon", "Arab")
    song.artist.origin = "Poland - Red Ruthenia - Świlcza"
    [found] = genius_lyrics_fetcher.get_lyrics("Arab", "Moon", songs=[song])
    assert (found.matched_artist, found.matched_title) == ("Arab (POL)", "Moon")
    assert asked == ["Arab Moon", "Arab (POL) Moon", "https://genius.com/Arab-pol-moon-lyrics"]
    assert [genius_lyrics_fetcher.country_codes(o) for o in ("#Japan", "England - London", None)] == [["JPN", "JP"], [], []]


def test_genius_also_tries_the_two_letter_country_code(monkeypatch):
    asked = []

    class Answer:
        def __init__(self, query):
            self.text = GENIUS_PAGE
            self.hits = ([{"type": "song", "result": {"id": 9, "title": "Buli, Buli", "primary_artist": {"name": "Dorina (HU)"},
                                                      "url": "https://genius.com/Dorina-hu-buli-buli-lyrics"}}]
                         if "(HU)" in query else [])

        def json(self):
            return {"response": {"sections": [{"hits": self.hits}]}}

    def get(url, **params):
        asked.append(params.get("q", url))
        return Answer(params.get("q", ""))

    monkeypatch.setattr(genius_lyrics_fetcher, "_get", get)
    song = _song("Buli, Buli", "Dorina")
    song.artist.origin = "Hungary"
    [found] = genius_lyrics_fetcher.get_lyrics("Dorina", "Buli, Buli", songs=[song])
    assert found.matched_artist == "Dorina (HU)"
    assert asked == ["Dorina Buli, Buli", "Dorina (HUN) Buli, Buli", "Dorina (HU) Buli, Buli",
                     "https://genius.com/Dorina-hu-buli-buli-lyrics"]


class _Page:
    def __init__(self, status, text="", encoding="utf-8"):
        self.status_code, self.text, self.encoding, self.apparent_encoding = status, text, encoding, "utf-8"


MUSIXMATCH_PAGE = ('<script id="__NEXT_DATA__">{"props": {"pageProps": {"data": {"trackInfo": {"data": '
                   '{"track": {"id": 7, "name": "Bez Przerwy Śmieję Się", "artistName": "Adam Aston", '
                   '"isInstrumental": false}, "lyrics": {"body": "Wstałem z łóżka\\nlewą nogą"}}}}}}}</script>')


def test_musixmatch_reads_the_page_its_names_make_trying_them_without_diacritics(monkeypatch):
    asked = []

    def get(url, **kw):
        asked.append(url)
        return _Page(200, MUSIXMATCH_PAGE) if "smieje" in url else _Page(404)

    monkeypatch.setattr(musixmatch_fetcher._pacer, "min_interval", 0)
    monkeypatch.setattr(lyrics_pages.requests, "get", get)
    [found] = musixmatch_fetcher.get_lyrics("Adam Aston", "Bez przerwy śmieję się")
    assert (found.matched_title, found.plain) == ("Bez Przerwy Śmieję Się", "Wstałem z łóżka\nlewą nogą")
    assert asked == ["https://www.musixmatch.com/lyrics/Adam-Aston/Bez-przerwy-%C5%9Bmiej%C4%99-si%C4%99",
                     "https://www.musixmatch.com/lyrics/Adam-Aston/Bez-przerwy-smieje-sie"]
    assert musixmatch_fetcher.slug("Help, I'm Alive") == "Help-I-m-Alive"


def test_musixmatch_failing_raises(monkeypatch):
    monkeypatch.setattr(musixmatch_fetcher._pacer, "min_interval", 0)
    monkeypatch.setattr(lyrics_pages.requests, "get", lambda url, **kw: _Page(403))
    with pytest.raises(LookupError):
        musixmatch_fetcher.get_lyrics("Adam Aston", "Bez przerwy")


LYRICSTRANSLATE_PAGE = ('<meta property="og:title" content="Afion - Okruglo zeljiče lyrics"/><div id="song-body">'
                        '<div class="par"><div>Okruglo zeljiče, čipkano lističe,</div><div>\nSedi si draga kraj mene.'
                        '</div></div><div class="par"><div>Kaj bi se ja sel</div></div></div>')


def test_lyricstranslate_reads_the_stanzas_of_the_page_its_names_make(monkeypatch):
    asked = []
    monkeypatch.setattr(lyricstranslate_fetcher._pacer, "min_interval", 0)
    monkeypatch.setattr(lyrics_pages.requests, "get",
                        lambda url, **kw: asked.append(url) or _Page(200, LYRICSTRANSLATE_PAGE))
    [found] = lyricstranslate_fetcher.get_lyrics("Afion", "Okruglo zeljiče")
    assert asked == ["https://lyricstranslate.com/en/Afion-Okruglo-Zeljice-lyrics.html"]
    assert (found.matched_artist, found.matched_title) == ("Afion", "Okruglo zeljiče")
    assert found.plain == "Okruglo zeljiče, čipkano lističe,\nSedi si draga kraj mene.\n\nKaj bi se ja sel"
    monkeypatch.setattr(lyrics_pages.requests, "get", lambda url, **kw: _Page(404))
    assert lyricstranslate_fetcher.get_lyrics("Afion", "Nothing") == []


TEKSTOWO_PAGE = ('<h1>Alina Gingertail - W moim ogródecku</h1><div id="songText"><div class="inner-text">'
                 'W moim ogródecku rośnie rózycka, <br/>\nNapój mi, Maniusiu <br/>\n<br/>\nNie chcę <br/>\n</div></div>')


def test_tekstowo_reads_the_page_its_names_make(monkeypatch):
    asked = []
    monkeypatch.setattr(tekstowo_fetcher._pacer, "min_interval", 0)
    monkeypatch.setattr(lyrics_pages.requests, "get",
                        lambda url, **kw: asked.append(url) or _Page(200, TEKSTOWO_PAGE))
    [found] = tekstowo_fetcher.get_lyrics("Alina Gingertail", "W moim ogródecku")
    assert asked == ["https://www.tekstowo.pl/alina-gingertail/w-moim-ogrodecku"]
    assert (found.matched_artist, found.matched_title) == ("Alina Gingertail", "W moim ogródecku")
    assert found.plain == "W moim ogródecku rośnie rózycka,\nNapój mi, Maniusiu\n\nNie chcę"
    assert tekstowo_fetcher.slug("Łańcuch I: Kiosk") == "lancuch-i-kiosk"


def test_tekstowo_spells_a_cyrillic_name_as_the_site_does(monkeypatch):
    asked = []
    monkeypatch.setattr(tekstowo_fetcher._pacer, "min_interval", 0)
    monkeypatch.setattr(lyrics_pages.requests, "get",
                        lambda url, **kw: asked.append(url) or _Page(200 if "krutitsa" in url else 404, TEKSTOWO_PAGE))
    assert tekstowo_fetcher.get_lyrics("Carla's Dreams", "Крутится Земля")
    assert asked[-1] == "https://www.tekstowo.pl/carla-s-dreams/krutitsa-zemla"


def test_lyricstranslate_falls_back_to_the_english_translations_page(monkeypatch):
    asked = []
    page = LYRICSTRANSLATE_PAGE.replace("Afion - Okruglo zeljiče lyrics", "CRAZY BLUES - Lot (Lot) (English translation)")
    page = page.replace('id="song-body"', 'id="original-lyrics"')
    monkeypatch.setattr(lyricstranslate_fetcher._pacer, "min_interval", 0)
    monkeypatch.setattr(lyrics_pages.requests, "get",
                        lambda url, **kw: asked.append(url) or _Page(200 if url.endswith("-english") else 404, page))
    [found] = lyricstranslate_fetcher.get_lyrics("CRAZY BLUES", "Lot")
    assert asked == ["https://lyricstranslate.com/en/CRAZY-BLUES-Lot-lyrics.html",
                     "https://lyricstranslate.com/en/crazy-blues-lot-english"]
    assert (found.matched_artist, found.matched_title) == ("CRAZY BLUES", "Lot (Lot)")


FAJNANUTA_PAGE = ('<title>▷ Anna Maria Jopek, Branford Marsalis - Patrz i Słuchaj TEKST - na Fajnanuta.pl</title>'
                  '<div class="arttxt">Właśnie tu <br/>\nwidzę<br/>\n</div><div class="arttxt">Translation</div>')


def test_fajnanuta_reads_the_first_text_of_the_page_its_names_make(monkeypatch):
    asked = []
    monkeypatch.setattr(fajnanuta_fetcher._pacer, "min_interval", 0)
    monkeypatch.setattr(lyrics_pages.requests, "get",
                        lambda url, **kw: asked.append(url) or _Page(200, FAJNANUTA_PAGE))
    [found] = fajnanuta_fetcher.get_lyrics("Anna Maria Jopek & Branford Marsalis", "Patrz I Słuchaj")
    assert asked == ["https://fajnanuta.pl/tekst-piosenki-tlumaczenie/anna-maria-jopek-branford-marsalis-patrz-i-sluchaj.html"]
    assert (found.matched_artist, found.matched_title, found.plain) == \
        ("Anna Maria Jopek, Branford Marsalis", "Patrz i Słuchaj", "Właśnie tu\nwidzę")


def test_a_page_of_another_site_gives_its_block_with_the_most_lines(monkeypatch):
    page = ("<html><div class='menu'><p>Home</p><p>Lyrics</p></div><script>var x = 1;</script>"
            "<div class='lcontent'>Izedi do<br>druga linia<br>trzecia<br><br>czwarta<br>piąta</div>"
            "<p>Two<br>lines</p></html>")
    monkeypatch.setattr(lyrics_pages.requests, "get", lambda url, **kw: _Page(200, page))
    assert lyrics_pages.lyrics_from_page("https://www.example.com/song.html") == \
        "Izedi do\ndruga linia\ntrzecia\n\nczwarta\npiąta"
    # A site a module reads goes through that module's reader
    monkeypatch.setattr(lyrics_pages.requests, "get", lambda url, **kw: _Page(200, TEKSTOWO_PAGE))
    assert lyrics_pages.lyrics_from_page("https://www.tekstowo.pl/x/y").startswith("W moim ogródecku")
    monkeypatch.setattr(lyrics_pages.requests, "get", lambda url, **kw: _Page(403))
    with pytest.raises(LookupError):
        lyrics_pages.lyrics_from_page("https://www.versuri.ro/versuri/x/")


def test_a_page_of_stanza_paragraphs_gives_them_all(monkeypatch):
    page = ("<div class='wpb_wrapper'><p>Menu</p><p>Raz<br>dwa<br>trzy<br>cztery</p><p>pięć<br>sześć</p>"
            "<p>siedem<br>osiem<br>dziewięć<br>dziesięć</p><p>Share</p></div>")
    monkeypatch.setattr(lyrics_pages.requests, "get", lambda url, **kw: _Page(200, page))
    assert lyrics_pages.lyrics_from_page("https://www.example.com/x") == \
        "Raz\ndwa\ntrzy\ncztery\n\npięć\nsześć\n\nsiedem\nosiem\ndziewięć\ndziesięć"


def test_a_poem_of_one_block_per_line_is_read_whole(monkeypatch):
    lines = "".join(f"<div>{line}</div>" for line in ["Ae fond kiss", "and then we sever", "Ae fareweel", "and then for ever",
                                                       "", "Deep in heart-wrung", "tears I'll pledge thee"])
    page = f"<html><nav><p>Poems</p></nav><div class='type-poem-text'>{lines}</div></html>"
    monkeypatch.setattr(lyrics_pages.requests, "get", lambda url, **kw: _Page(200, page))
    assert lyrics_pages.lyrics_from_page("https://www.poetryfoundation.org/poems/1") == (
        "Ae fond kiss\nand then we sever\nAe fareweel\nand then for ever\n\nDeep in heart-wrung\ntears I'll pledge thee")


def test_pesna_org_gives_the_original_not_the_page_around_it(monkeypatch):
    page = ("<div><p>Click to play</p><p>Share</p><p>Print</p><p>Translations</p><p>English</p></div>"
            "<div id='lang-mk' class='lyrics'><h3>Стојана</h3><div class='whitespace-pre-line'>Ред еден\nРед два</div></div>")
    monkeypatch.setattr(lyrics_pages.requests, "get", lambda url, **kw: _Page(200, page))
    assert lyrics_pages.lyrics_from_page("https://pesna.org/song/871") == "Ред еден\nРед два"


@pytest.mark.parametrize("url, page", [
    ("https://sonichits.com/video/A/B", "<div class='menu'><p>a</p><p>b</p><p>c</p><p>d</p><p>e</p></div>"
                                        "<div id='dLyrics' class='lyrics-text'><div>Ad</div>Raz<br/>Dwa</div>"),
    ("https://teksty-pesenok.pro/en/1/A/B", "<div class='content__filter-box'><p>A</p><p>B</p><p>C</p><p>D</p></div>"
                                            "<div id='music__text'>Raz<br/>Dwa</div><div id='music__translate'>One<br/>Two</div>"),
    # a reader keyed by the domain a site is under
    ("https://lyrics.alternatifim.com/artist/a/b", "<div class='list'><p>a</p><p>b</p><p>c</p><p>d</p></div>"
                                                   "<div class='sarkisozu'>Raz<br/>Dwa</div>"),
    ("https://tekstovi.net/2,1,2.html", "<ul class='list'><li>a</li><li>b</li><li>c</li><li>d</li></ul>"
                                       "<article><section class='lyrics'>Raz<br/>Dwa</section></article>"),
    ("https://any.blogspot.com/2012/12/b.html", "<div class='tags'><p>a</p><p>b</p><p>c</p><p>d</p><p>e</p></div>"
                                                "<div class='post-body entry-content'>Raz<br/>Dwa</div>"),
])
def test_sites_with_a_reader_of_their_own(monkeypatch, url, page):
    monkeypatch.setattr(lyrics_pages.requests, "get", lambda u, **kw: _Page(200, page))
    assert lyrics_pages.lyrics_from_page(url).endswith("Raz\nDwa")


def test_a_page_read_by_hand_is_saved_on_a_yes(lyrics_db, monkeypatch):
    song = _song(song_id=11)

    class Text:
        def __init__(self, question, **kw):
            pass

        def ask(self):
            return " https://www.example.com/song.html "

    monkeypatch.setattr("questionary.text", Text)
    monkeypatch.setattr(lyrics_review, "lyrics_from_page", lambda url: "A\nB")
    monkeypatch.setattr(lyrics_review, "ask_yes_no", lambda question: True)
    with store.lyrics_db_session(lyrics_db) as session:
        lyrics_review._from_page(session, song)
        assert store.stored_lyrics(session, 11) == store.Lyrics("A\nB", None, False, "manual",
                                                                "https://www.example.com/song.html")


def test_words_win_over_a_claim_of_none():
    claim = LyricsDiscoveryResult(instrumental=True, matched_title="Jump Up", matched_artist="Crookers", duration_s=222)
    match = dm.discover_lyrics(_song("Jump Up", "Crookers"), _module(claim, _found("Jump Up", "Crookers")), 222).match
    assert not match.result.instrumental and match.result.plain


def test_a_found_title_naming_another_version_is_not_ours():
    search = dm.discover_lyrics(_song("最果て", "SennaRin"),
                                _module(LyricsDiscoveryResult(instrumental=True, matched_title="最果て (Instrumental)",
                                                              matched_artist="SennaRin", duration_s=200),
                                        _found("最果て", "SennaRin")), 200)
    assert search.match.sure and search.match.result.matched_title == "最果て"


def test_the_latin_form_is_asked_and_a_dashed_note_is_like_a_bracket():
    song = _song("映画の中の出来事", "Atlantis Airport", title_latin="Eiga no Naka no Dekigoto")
    assert ("Atlantis Airport", "Eiga no Naka no Dekigoto") in dm._lyrics_queries(song)
    match = dm.discover_lyrics(_song("Nazywali go marynarz", "Artur Andrus"),
                               _module(_found("Nazywali go Marynarz – szanta narciarska", "Artur Andrus")), 200).match
    assert match.sure


def test_cuspajz_reads_the_page_its_names_make_and_its_index_is_nothing(monkeypatch):
    page = ("<title>Cinkuši — Raca — Tekst</title><article><h1>Cinkuši Raca</h1>"
            "<p class='text clearfix'>Raca plava<br/>\npo vodi</p></article>")
    asked = []
    monkeypatch.setattr(cuspajz_fetcher._pacer, "min_interval", 0)
    monkeypatch.setattr(lyrics_pages.requests, "get", lambda url, **kw: asked.append(url) or _Page(200, page))
    [found] = cuspajz_fetcher.get_lyrics("Cinkuši", "Raca")
    assert asked == ["https://cuspajz.com/tekstovi-pjesama/pjesma/cinkusi/raca.html"]
    assert (found.matched_artist, found.matched_title, found.plain) == ("Cinkuši", "Raca", "Raca plava\npo vodi")
    monkeypatch.setattr(lyrics_pages.requests, "get", lambda url, **kw: _Page(200, "<title>Tekstovi pjesama</title>"))
    assert cuspajz_fetcher.get_lyrics("Nobody", "Nothing") == []
    assert cuspajz_fetcher.slug("Đorđe Balašević") == "dorde-balasevic"


def test_pasted_lyrics_are_saved_as_the_owners(lyrics_db, monkeypatch):
    song = _song(song_id=9)

    class Text:
        def __init__(self, question, default="", multiline=False):
            assert multiline and default == "From the clipboard"

        def ask(self):
            return "  Typed\nby hand  "

    monkeypatch.setattr(lyrics_review.pyperclip, "paste", lambda: "From the clipboard\n")
    monkeypatch.setattr("questionary.text", Text)
    with store.lyrics_db_session(lyrics_db) as session:
        lyrics_review._paste(session, song)
        assert store.stored_lyrics(session, 9) == store.Lyrics("Typed\nby hand", None, False, "manual")


@pytest.mark.parametrize("text, plain, synced", [
    ("Raz\nDwa", "Raz\nDwa", None),
    ("[00:01.00] Raz\n[00:02.50]Dwa", "Raz\nDwa", "[00:01.00] Raz\n[00:02.50]Dwa"),
])
def test_library_files_lyrics_tags(tmp_path, monkeypatch, text, plain, synced):
    path = tmp_path / "song.mp3"
    path.write_bytes(b"")
    tags = ID3()
    tags.add(USLT(encoding=3, lang="pol", desc="", text=text))
    tags.save(path)
    monkeypatch.setattr(library_tags_fetcher, "song_file_paths", lambda ids: {ids[0]: [str(path)]})
    [found] = library_tags_fetcher.get_lyrics("A", "T", songs=[Song(id=5, title="T")])
    assert (found.plain, found.synced, found.from_song_file) == (plain, synced, True)


# ---- the review list ----

def test_the_review_saves_the_ticked_and_may_reject_the_rest(lyrics_db, monkeypatch):
    first, second = _song("Kometa"), _song("Wojenka")
    first.id, second.id = 1, 2
    unsure = [(first, dm.LyricsMatch(_found("Kometa"), "m", False, None, 200)),
              (second, dm.LyricsMatch(_found("Wojenka (Live)"), "m", False, 200, 205, False))]
    seen = {}

    class Checkbox:
        def __init__(self, question, choices):
            seen["checked"] = [c.checked for c in choices]

        def ask(self):
            return [0]

    monkeypatch.setattr("questionary.checkbox", Checkbox)
    monkeypatch.setattr(lyrics_review, "ask_yes_no", lambda question: True)
    with store.lyrics_db_session(lyrics_db) as session:
        assert lyrics_review._review_unsure(session, unsure) == 1
        assert seen["checked"] == [True, False]  # an equal title ticked, a resembling one not
        assert store.stored_lyrics(session, 1).source == "m"
        assert store.answered_modules(session) == {2: {"m"}}
        assert session.get(store.ModuleMiss, (2, "m")).reason == LYRICS_MISS_REJECTED


def test_the_instrumental_list_marks_the_ticked_and_turns_down_an_unticked_claim(lyrics_db, monkeypatch):
    claimed, nothing, wrong = _song("Intro"), _song("Outro"), _song("Kometa")
    claimed.id, nothing.id, wrong.id = 1, 2, 3
    says = LyricsDiscoveryResult(instrumental=True, matched_title="x", matched_artist="y")
    wordless = [(claimed, dm.LyricsMatch(says, "genius_lyrics_fetcher", True)), (nothing, None),
                (wrong, dm.LyricsMatch(says, "lrclib_fetcher", True))]
    seen = {}

    class Checkbox:
        def __init__(self, question, choices):
            seen["checked"] = [c.checked for c in choices]

        def ask(self):
            return [0, 1]

    monkeypatch.setattr("questionary.checkbox", Checkbox)
    monkeypatch.setattr(lyrics_review, "submit_global_database_session", lambda: None)
    with store.lyrics_db_session(lyrics_db) as session:
        lyrics_review._offer_instrumental(session, wordless)
        assert seen["checked"] == [True, False, True]
        assert store.stored_lyrics(session, 1).instrumental
        assert store.answered_modules(session) == {3: {"lrclib_fetcher"}}
    assert [s.language for s in (claimed, nothing, wrong)] == ["Instrumental", "Instrumental", None]


# ---- merges and deletions ----

def _replay(path, changes):
    conn = sqlite3.connect(path)
    song_id_replay.replay(conn, changes, has_tags=False, has_lyrics=True)
    conn.commit()
    rows = dict(conn.execute("SELECT song_id, plain FROM lyrics"))
    misses = sorted(conn.execute("SELECT song_id, module_id FROM lyrics_module_misses"))
    conn.close()
    return rows, misses


def test_merges_move_lyrics_and_answers_to_the_kept_song_unless_it_has_its_own(lyrics_db):
    with store.lyrics_db_session(lyrics_db) as session:
        for song_id, text in ((1, "one"), (2, "two"), (3, "three")):
            store.save_lyrics(session, song_id, store.Lyrics(plain=text, source="m"))
        store.record_miss(session, _song(song_id=4), "a")
        store.record_miss(session, _song(song_id=5), "a")
        store.record_miss(session, _song(song_id=5), "b")
        store.record_miss(session, _song(song_id=6), "a")
        session.commit()
    # 1 -> 4 (4 had only a miss), 2 -> 3 (3 has its own), 5 -> 4 (4 answered for "a"), 6 deleted
    rows, misses = _replay(lyrics_db, [(1, 1, 4), (2, 2, 3), (3, 5, 4), (4, 6, None)])
    assert rows == {3: "three", 4: "one"}
    assert misses == [(4, "a"), (4, "b")]


# ---- the sharing server ----

def test_the_server_answers_one_song_following_merges(tmp_path, lyrics_db):
    music = tmp_path / "share-music.db"
    conn = sqlite3.connect(music)
    conn.executescript("CREATE TABLE song_id_changes (id INTEGER PRIMARY KEY, old_song_id INTEGER, "
                       "new_song_id INTEGER); INSERT INTO song_id_changes VALUES (1, 2, 4), (2, 5, NULL);")
    conn.close()
    with store.lyrics_db_session(lyrics_db) as session:
        store.save_lyrics(session, 4, store.Lyrics(plain="a", synced="[00:01.00] a", source="lrclib_fetcher"))
        store.save_lyrics(session, 5, store.Lyrics(instrumental=True, source="lrclib_fetcher"))
        session.commit()
    expected = {"song_id": 4, "plain": "a", "synced": "[00:01.00] a", "instrumental": False,
                "source": "lrclib_fetcher"}
    assert sharing_server.song_lyrics(str(lyrics_db), str(music), 4) == expected
    assert sharing_server.song_lyrics(str(lyrics_db), str(music), 2) == expected
    assert sharing_server.song_lyrics(str(lyrics_db), str(music), 5) is None  # deleted since
    assert sharing_server.song_lyrics(str(lyrics_db), str(music), 9) is None


def test_lyrics_tables_never_reach_the_public_copy():
    assert {"lyrics", "lyrics_module_misses"} <= public_export._private_table_names()
