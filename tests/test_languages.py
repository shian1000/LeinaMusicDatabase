"""Songs' languages (songs.language; utils/common/language_names.py,
utils/database/languages.py): one spelling per language on every ORM write
(Song's validator) and Manage database -> "Unify language and country names" for rows
stored before (utils/ui/language_review.py, the checkbox list answered by the
test); reading a language (langdetect on the lyrics, Musixmatch, MusicBrainz),
discoveries_manager.discover_language() and Fill missing data -> Languages
with the modules faked. In-memory SQLite, the web faked."""

import sys
from pathlib import Path

import pytest
import questionary
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "src"))

from utils.common.language_names import clean_language
from utils.database.datatables import Artist, Base, Song
from utils.database.languages import spelling_fixes
from utils.ui import language_review


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.mark.parametrize("typed, stored", [
    ("english", "English"),
    ("Finish", "Finnish"),  # LANGUAGE_SYNONYMS
    ("Slovakian", "Slovak"),
    ("Nigerian Pigdin / Yoruba", "Nigerian Pidgin / Yoruba"),
    ("eng", "English"),  # ISO codes as mp3 tags write them
    ("de", "German"),
    ("ger", "German"),
    ("ell", "Greek"),  # pycountry's "Modern Greek (1453-)"
    ("zxx", "Instrumental"),
    ("und", "Unknown"),
    ("English, Japanese", "English / Japanese"),
    ("English/English;english", "English"),
    ("Unknown / Polish", "Polish"),
    ("unknown", "Unknown"),
    ("Ga / Jamaican Patois", "Ga / Jamaican Patois"),  # capitalised: a name, not Irish's "ga"
    ("Croatian (Kajkavian)", "Croatian (Kajkavian)"),  # the owner's own names stay
    ("Made-up Latin", "Made-up Latin"),
    ("  ", None),
    (None, None),
])
def test_one_spelling_per_language(typed, stored):
    assert clean_language(typed) == stored


def test_every_orm_write_is_cleaned(session):
    song = Song(title="Sonne", artist=Artist(name="Rammstein"), language="ger")
    session.add(song)
    session.flush()
    song.language = "english / pol"

    assert song.language == "English / Polish"


def _stored(session, *languages):
    """Songs with these values as older rows have them, past the validator."""
    artist = Artist(name="A")
    session.add(artist)
    session.flush()
    for i, language in enumerate(languages):
        session.execute(text("INSERT INTO songs (title, artist_id, language) VALUES (:t, :a, :l)"),
                        {"t": f"Song {i}", "a": artist.id, "l": language})


def test_only_spellings_that_change_are_listed_most_used_first(session):
    _stored(session, "Finish", "english", "english", "Polish", "Kajkavian", None)

    assert spelling_fixes(session, "language") == [("english", "English", 2), ("Finish", "Finnish", 1)]


def _tick(monkeypatch, pick):
    """Answer the checkbox list with the choices `pick(titles)` names."""
    def checkbox(_question, choices):
        titles = [choice.title for choice in choices]
        assert all(choice.checked for choice in choices)
        picked = [choice.value for choice in choices if choice.title in pick(titles)]
        return type("Prompt", (), {"ask": lambda self: picked})()
    monkeypatch.setattr(questionary, "checkbox", checkbox)


def test_unify_changes_only_the_ticked_spellings(session, monkeypatch):
    _stored(session, "Finish", "Finish", "Portugeese", "Polish")
    monkeypatch.setattr(language_review, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(language_review, "submit_global_database_session", session.commit)
    _tick(monkeypatch, lambda titles: [t for t in titles if t.startswith("language: 'Finish'")])

    assert language_review.unify_language_and_country_names() == 2
    languages = sorted(language for (language,) in session.execute(text("SELECT language FROM songs")))
    assert languages == ["Finnish", "Finnish", "Polish", "Portugeese"]


def test_unify_cancelled_changes_nothing(session, monkeypatch):
    _stored(session, "Finish")
    monkeypatch.setattr(language_review, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(language_review, "submit_global_database_session", session.commit)
    monkeypatch.setattr(questionary, "checkbox",
                        lambda *a, **k: type("Prompt", (), {"ask": lambda self: None})())

    assert language_review.unify_language_and_country_names() == 0
    assert session.execute(text("SELECT language FROM songs")).scalar() == "Finish"


def test_nothing_to_unify(session, monkeypatch):
    _stored(session, "English", "Polish / English")
    monkeypatch.setattr(language_review, "open_and_set_global_database_sessions", lambda: (session, None))

    assert language_review.unify_language_and_country_names() == 0


# ==================== reading a language ====================

BELARUSIAN = ("Жыве Беларусь, мы жывем у нашай краіне і спяваем песні пра свабоду і любоў, пра родны край і "
              "хату, дзе нас чакаюць")
UKRAINIAN = "Ой у лузі червона калина похилилася, чогось наша славна Україна зажурилася, а ми тую червону калину"
ENGLISH = ("I was walking down the street when I saw you standing in the rain and I knew that I would love "
           "you forever and ever")
CROATIAN = ("Prošla džungle i tundre tek sam počela malo je, sve je sad zbog tebe i ljeti i zimi klimam glavom "
            "cijeli dan dok sunce ne zađe")
EKAVIAN = ("Koju pesmu hoćete? Nebo je plavo i ja sam ovde sa tobom sve do kraja ovog dana i noći, ne idem nigde, "
           "ostajem ovde zauvek")


def test_the_alphabet_and_letters_narrow_the_languages():
    from utils.common.language_names import language_of_text

    assert language_of_text(BELARUSIAN)[0] == "Belarusian"  # langdetect knows none
    assert language_of_text(UKRAINIAN)[0] == "Ukrainian"
    assert language_of_text(ENGLISH)[0] == "English"
    assert language_of_text("Хайрын зөөлөн мэлмийгээр намайг чи ширтэнэ дээ, хамгийн сайхан үгээр намайг чи "
                            "дуудна даа үүрд мөнхөд")[0] == "Mongolian"
    assert language_of_text("Мен сені сүйемін, қазақ жерім, әнім менің, туған елім, ғасырлар бойы ән шырқаймын "
                            "далада, ата-анам, туған жерім")[0] == "Kazakh"
    assert language_of_text("Две лири, една песна, ѕвездите сјаат над Охрид, ќе те чекам до зори, моја мила "
                            "девојко, ќе те сакам секогаш")[0] == "Macedonian"
    assert language_of_text("אַז דער רבי אלימלך איז געווען זייער פֿריילעך, האָט ער אויסגעטאָן די תפֿילין און "
                            "אָנגעטאָן די ברילן, און ער האָט גערופֿן די פֿידלער")[0] == "Yiddish"
    # ŭ is Lacinka; č / š / ž with ś and ł too - but not a Polish text holding one name with č
    assert language_of_text("Biehla staroža z čystaha polia, dzie ŭ travie hulaje viecier i niasie halasy pa "
                            "ŭsim lesie i ŭsim poli")[0] == "Belarusian"
    assert language_of_text("Jechałem przez miasto, Ivan Kačić śpiewał w radiu, a ja myślałem o tobie przez cały "
                            "dzień i noc, aż do rana")[0] == "Polish"


def test_a_second_script_adds_its_language_and_credits_are_no_lyrics():
    from utils.common.language_names import language_of_text

    korean = ("여긴 서울특별시 우리가 사는 곳 오늘도 내일도 영원히 함께해 사랑해 너를 우리는 언제나 여기 있어 "
              "we live here every day and night, we love it here, this is our home and our city forever")
    assert language_of_text(korean)[0] == "Korean / English"
    assert language_of_text("Lời: Dennis Lyxzén / David Sandström\nNhạc: Refused\nI never fade away") is None
    assert language_of_text("Lake") is None  # too few letters to tell


@pytest.mark.parametrize("text, title, origin, others, year, language", [
    (CROATIAN, "Savana", None, ["Bosnian", "Bosnian"], 2017, "Bosnian"),  # the artist's other songs
    (CROATIAN, "Klasika", None, [], 1984, "Serbo-Croatian"),  # made in Yugoslavia
    (CROATIAN, "Pjesma", "Croatia - Zagreb", [], 1990, "Serbo-Croatian"),
    (CROATIAN, "Pjesma", "Croatia - Zagreb", [], 1995, "Croatian"),
    (EKAVIAN, "Nebo", "#Serbia - Belgrade", [], 2004, "Serbo-Croatian"),  # Serbia left in 2006
    (EKAVIAN, "Suženi snovi", None, [], 2016, "Serbian"),  # ekavian: Serbia
    (CROATIAN, "Pjesma", "Montenegro - Podgorica", [], 2010, "Serbian"),
    (CROATIAN.replace("sunce", "śunce"), "Pjesma", "Montenegro - Podgorica", [], 2010, "Montenegrin"),
    (CROATIAN, "Brijuni", "Bosna and Herzegovina", [], 2013, "Bosnian"),  # the owner's spelling of the country
    (CROATIAN, "Pjesma", "Crna Gora", [], 2010, "Serbian"),
])
def test_serbian_croatian_bosnian_go_by_country_and_year(text, title, origin, others, year, language):
    from utils.common.language_names import song_language

    assert song_language(text, title, "Artist", origin, others, year)[:3:2] == (language, False)


def test_without_the_artists_country_a_later_song_waits_for_it():
    from utils.common.language_names import song_language

    assert song_language(CROATIAN, "Pjesma", "Artist", None, [], 2015)[::2] == ("Croatian", True)
    assert song_language(CROATIAN, "Pjesma", "Artist", None, [], None)[::2] == ("Croatian", True)


def test_lyrics_in_a_language_the_titles_script_doesnt_write_may_be_a_translation():
    from utils.common.language_names import may_be_translation, song_language

    assert may_be_translation("ビバリウム", "English") and may_be_translation("Капитал", "English")
    # Lacinka under a Cyrillic title, romaji under a Japanese one, a mixed value or a Latin title: no
    assert not may_be_translation("Палон", "Belarusian")
    assert not may_be_translation("뛰어", "Korean / English")
    assert not may_be_translation("夜のシルエット (Night Silhouette)", "Japanese")
    assert not may_be_translation("Herz", "English")
    assert song_language(ENGLISH, "ビバリウム", "Ado")[:2] == ("English", 0.5)  # never saved without the owner


def test_romaji_and_rarely_right_readings():
    from utils.common.language_names import song_language

    romaji = ("Machi no zawameki ga mimi ni nokoru yoru no shiruetto kimi no koe ga kikoeru kimi to futari de "
              "aruita michi")
    assert song_language(romaji, "夜のシルエット", "Chill City Pop")[0] == "Japanese"
    assert song_language(romaji, "Night Silhouette", "Chill City Pop", None, ["Japanese", "Japanese"])[0] == "Japanese"
    language, probability, _ = song_language(romaji, "Night Silhouette", "Chill City Pop")
    if language in ("Swahili", "Somali", "Yoruba"):  # whatever langdetect makes of it, never sure
        assert probability <= 0.5


def test_stored_lyrics_module_reads_the_songs_own_text(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from utils.database.lyrics_store import Lyrics
    from utils.discoveries.discovery_modules import stored_lyrics_fetcher as module

    stored = {1: Lyrics(plain=ENGLISH, source="lrclib"),
              2: Lyrics(synced="[00:01.00] I was walking down the street when I saw you\n[00:04.50] standing in "
                               "the rain and I knew that I would love you forever and ever", source="lrclib"),
              3: Lyrics(instrumental=True, source="musixmatch_fetcher"),
              5: Lyrics(plain=CROATIAN, source="lrclib")}
    monkeypatch.setattr(module, "lyrics_db_session", contextmanager(lambda: (yield None)))
    monkeypatch.setattr(module, "stored_lyrics", lambda session, song_id: stored.get(song_id))
    artist = SimpleNamespace(name="Sassja", origin=None, songs=[])
    artist.songs = [SimpleNamespace(language="Bosnian"), SimpleNamespace(language="Unknown")]
    song = lambda song_id, year=None: [SimpleNamespace(id=song_id, title="T", artist=artist, year=year)]

    found = module.get_language("A", "T", songs=song(1))
    assert (found.language, found.from_song, found.confidence > 0.9) == ("English", True, True)
    assert module.get_language("A", "T", songs=song(2)).language == "English"  # LRC times left out
    assert (module.get_language("A", "T", songs=song(3)).language, module.get_language("A", "T", songs=song(3))
            .confidence) == ("Instrumental", None)
    assert module.get_language("A", "T", songs=song(4)) is None
    assert module.get_language("A", "T", songs=song(5, 2017)).language == "Bosnian"  # the artist's other songs


class _Page:
    def __init__(self, status, text=""):
        self.status_code, self.text, self.encoding, self.apparent_encoding = status, text, "utf-8", "utf-8"


def _musixmatch_page(language: str, instrumental: bool = False) -> str:
    return ('<script id="__NEXT_DATA__">{"props": {"pageProps": {"data": {"trackInfo": {"data": '
            '{"track": {"id": 7, "name": "Arahja", "artistName": "Kult", "isInstrumental": %s}, '
            '"lyrics": {"body": "Nie ma", "language": "%s"}}}}}}}</script>' % (str(instrumental).lower(), language))


def test_musixmatch_names_the_language_of_its_lyrics(monkeypatch):
    from utils.discoveries import lyrics_pages
    from utils.discoveries.discovery_modules import musixmatch_fetcher

    monkeypatch.setattr(musixmatch_fetcher._pacer, "min_interval", 0)
    page = {"text": _musixmatch_page("pl")}
    monkeypatch.setattr(lyrics_pages.requests, "get", lambda url, **kw: _Page(200, page["text"]))

    found = musixmatch_fetcher.get_language("Kult", "Arahja")
    assert (found.language, found.matched_title, found.matched_artist, found.confidence) == (
        "Polish", "Arahja", "Kult", None)
    page["text"] = _musixmatch_page("", instrumental=True)
    assert musixmatch_fetcher.get_language("Kult", "Arahja").language == "Instrumental"
    page["text"] = _musixmatch_page("")
    assert musixmatch_fetcher.get_language("Kult", "Arahja") is None


def test_musicbrainz_takes_the_language_of_the_first_recording_linked_to_its_work(monkeypatch):
    from utils.discoveries.discovery_modules import music_brainz_fetcher as mb

    answers = {
        "recording/": {"recordings": [
            {"id": "other", "title": "Polska", "artist-credit": [{"name": "Kult"}]},
            {"id": "r1", "title": "Arahja", "artist-credit": [{"name": "Kult"}]},
            {"id": "r2", "title": "Arahja", "artist-credit": [{"name": "Kult", "joinphrase": " & "},
                                                              {"name": "Kazik"}]}]},
        "recording/r1": {"relations": []},  # not linked to its work
        "recording/r2": {"relations": [{"type": "performance", "work": {"languages": ["pol", "eng"]}}]},
    }
    asked = []

    def mb_get(url, params):
        key = url.split("/ws/2/")[1]
        asked.append(key)
        return type("Response", (), {"json": lambda self: answers[key]})()

    monkeypatch.setattr(mb, "mb_get", mb_get)
    found = mb.get_language("Kult", "Arahja")
    assert (found.language, found.matched_artist, found.source_ref) == ("Polish / English", "Kult & Kazik", "r2")
    assert asked == ["recording/", "recording/r1", "recording/r2"]  # "Polska" is another song

    monkeypatch.setattr(mb, "mb_get", lambda url, params: None)
    with pytest.raises(ConnectionError):  # asked again next time, not remembered as unknown
        mb.get_language("Kult", "Arahja")


# ==================== discover_language() ====================

def _module(answer=None, local=False, fails=False):
    """A get_language() module answering `answer(artist, title)`."""
    from types import SimpleNamespace
    calls = []

    def get_language(artist, title, songs=None):
        calls.append((artist, title))
        if fails:
            raise ConnectionError("no network")
        return answer(artist, title) if answer else None
    return SimpleNamespace(get_language=get_language, LOCAL=local, calls=calls)


def _result(language, confidence=None, from_song=False, title="Arahja", artist="Kult"):
    from utils.discoveries.discovery_result import LanguageDiscoveryResult
    return LanguageDiscoveryResult(language, matched_title=None if from_song else title,
                                   matched_artist=None if from_song else artist, confidence=confidence,
                                   from_song=from_song)


def test_a_sure_answer_after_an_unsure_one_wins(session):
    from utils.discoveries.discoveries_manager import discover_language

    song = Song(title="Arahja", artist=Artist(name="Kult"))
    session.add(song)
    session.flush()
    lyrics = _module(lambda a, t: _result("Russian", 0.7, from_song=True), local=True)
    broken = _module(fails=True)
    other_song = _module(lambda a, t: _result("English", title="Polska"))
    site = _module(lambda a, t: _result("Polish"))

    search = discover_language(song, [("lyrics", "L", lyrics), ("broken", "B", broken),
                                      ("other", "O", other_song), ("site", "S", site)])

    assert (search.match.module_id, search.match.result.language, search.match.sure) == ("site", "Polish", True)
    assert (search.failed, search.missed) == (["broken"], ["other"])
    assert len(lyrics.calls) == 1  # a local module reads the song itself, once


def test_an_unsure_reading_is_kept_when_nothing_is_sure(session):
    from utils.discoveries.discoveries_manager import discover_language

    song = Song(title="Arahja", artist=Artist(name="Kult"))
    session.add(song)
    session.flush()
    search = discover_language(song, [("lyrics", "L", _module(lambda a, t: _result("Polish", 0.8, from_song=True),
                                                              local=True)),
                                      ("site", "S", _module())])
    assert (search.match.result.language, search.match.sure, search.missed) == ("Polish", False, ["site"])


# ==================== Fill missing data -> Languages ====================

def test_fill_missing_languages(session, monkeypatch):
    kult, solo = Artist(name="Kult"), Artist(name="Solo")
    session.add_all([Song(title=f"Known {i}", artist=kult, language="Polish") for i in range(3)])
    arahja = Song(title="Arahja", artist=kult, language="Unknown")
    romaji = Song(title="Kimi", artist=kult, language="Unknown")
    nothing = Song(title="Silence", artist=kult)
    online = Song(title="Away", artist=solo, language="Unknown")
    session.add_all([arahja, romaji, nothing, online])
    session.flush()

    readings = {arahja.id: _result("Polish", 0.99, from_song=True), romaji.id: _result("Swahili", 0.99, from_song=True)}
    lyrics = _module(local=True)
    lyrics.get_language = lambda artist, title, songs=None: readings.get(songs[0].id)
    site = _module(lambda a, t: _result("English", title="Away", artist="Solo") if t == "Away" else None)
    monkeypatch.setattr(language_review, "load_discovery_modules",
                        lambda kind: [("stored_lyrics_fetcher", "L", lyrics), ("site", "S", site)])
    monkeypatch.setattr(language_review, "load_max_songs_per_fetch", lambda: 50)
    monkeypatch.setattr(language_review, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(language_review, "submit_global_database_session", session.commit)
    shown = {}

    def checkbox(_question, choices):
        shown.update({choice.title: choice.checked for choice in choices})
        picked = [choice.value for choice in choices if choice.checked]
        return type("Prompt", (), {"ask": lambda self: picked})()
    monkeypatch.setattr(questionary, "checkbox", checkbox)

    assert language_review.fill_missing_languages() == 3
    assert (arahja.language, online.language) == ("Polish", "English")  # saved at once
    # a sure reading none of the artist's songs has is asked about, unticked
    assert shown == {"Kult - Kimi  ->  Swahili  (lyrics, 0.99; the artist: 0/3 songs)": False,
                     "Kult - Silence  ->  Polish  (the artist's other songs; the artist: 3/3 songs)": True}
    assert (romaji.language, nothing.language) == ("Unknown", "Polish")


def _fill_with(session, monkeypatch, readings, site=None):
    lyrics = _module(local=True)
    lyrics.get_language = lambda artist, title, songs=None: readings.get(songs[0].id)
    modules = [("stored_lyrics_fetcher", "L", lyrics)] + ([("site", "S", site)] if site else [])
    monkeypatch.setattr(language_review, "load_discovery_modules", lambda kind: modules)
    monkeypatch.setattr(language_review, "load_max_songs_per_fetch", lambda: 50)
    monkeypatch.setattr(language_review, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(language_review, "submit_global_database_session", session.commit)


@pytest.mark.parametrize("type_now", [True, False])
def test_songs_needing_the_artists_origin_are_typed_or_left_for_later(session, monkeypatch, type_now):
    from utils.discoveries.discovery_result import LanguageDiscoveryResult

    nameless, croatian = Artist(name="Elemental"), Artist(name="Gibonni", origin="Croatia - Dalmatia - Split")
    waits = Song(title="Usne", artist=nameless, year=2016, language="Unknown")
    site_waits = Song(title="Srce", artist=nameless, year=2016, language="Unknown")
    classic = Song(title="Zlatne godine", artist=croatian, year=1989, language="Unknown")
    session.add_all([waits, site_waits, classic])
    session.flush()
    reading = LanguageDiscoveryResult("Croatian", confidence=1.0, from_song=True, needs_origin=True)
    site = _module(lambda a, t: _result("Croatian", title=t, artist=a))
    _fill_with(session, monkeypatch, {waits.id: reading}, site)
    asked = []
    monkeypatch.setattr(language_review, "ask_yes_no", lambda question: asked.append(question) or type_now)
    typed = iter(["Serbian", ""])
    monkeypatch.setattr(questionary, "text", lambda *a, **k: type("Prompt", (), {"ask": lambda self: next(typed)})())

    language_review.fill_missing_languages()

    assert classic.language == "Serbo-Croatian"  # the site's Croatian, under the country-and-year rule
    assert len(asked) == 1
    if type_now:
        assert (waits.language, site_waits.language) == ("Serbian", "Unknown")  # empty = later
    else:
        assert (waits.language, site_waits.language) == ("Unknown", "Unknown")
