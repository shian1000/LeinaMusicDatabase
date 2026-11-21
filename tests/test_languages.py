"""Songs' languages (songs.language; utils/common/language_names.py,
utils/database/languages.py): one spelling per language on every ORM write
(Song's validator) and Manage database -> "Unify language and country names" for rows
stored before (utils/ui/language_review.py, the checkbox list answered by the
test); reading a language (langdetect on the lyrics, Musixmatch, MusicBrainz,
YouTube's audio language of the song's video with the Data API faked),
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
    ("Kajkavian", "Croatian (Kajkavian)"),
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
    _stored(session, "Finish", "english", "english", "Polish", "Interslavic", None)

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
    from types import SimpleNamespace
    from utils.common.language_names import may_be_translation, song_language

    assert may_be_translation("ビバリウム", "English") and may_be_translation("Капитал", "English")
    # Lacinka under a Cyrillic title, romaji under a Japanese one, a mixed value or a Latin title: no
    assert not may_be_translation("Палон", "Belarusian")
    assert not may_be_translation("뛰어", "Korean / English")
    assert not may_be_translation("夜のシルエット (Night Silhouette)", "Japanese")
    assert not may_be_translation("Herz", "English")
    # Cyrillic letters the language hasn't: a Ukrainian title isn't Russian, a Russian one isn't Belarusian
    assert may_be_translation("ДАЛІ МИ САМІ", "Russian") and may_be_translation("Завируха", "Belarusian")
    assert not may_be_translation("ДАЛІ МИ САМІ", "Ukrainian") and not may_be_translation("ДАЛІ МИ САМІ", "Russian / Ukrainian")
    # not ticked at first, however many of the artist's songs are Russian (Сергій Бабкін)
    babkin = SimpleNamespace(title="ДАЛІ МИ САМІ")
    assert not language_review.LanguageProposal(babkin, "Russian", False, None, 1.0, 3).checked
    assert language_review.LanguageProposal(babkin, "Ukrainian", False, None, 1.0, 3).checked
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


def test_a_text_holding_a_translation_is_not_read():
    from utils.common.language_names import language_of_text, looks_like_translation

    english = ENGLISH.split(" and ")
    russian = ["Я шёл по улице, когда увидел тебя", "стоящей под дождём, и я знал", "что буду любить тебя",
               "вечно и вечно"]
    interleaved = "\n".join(line for pair in zip(english * 4, russian * 4) for line in pair)
    assert looks_like_translation(interleaved) and language_of_text(interleaved) is None
    assert looks_like_translation("Eva (оригинал Boombox)\nЕва [Навсегда] (перевод)\n" + ENGLISH)
    vietnamese = "\n".join(line for pair in zip(english * 4, ["Ta đã đánh mất mọi thứ", "Và phải trả giá đắt",
                                                              "Ở nơi em, anh từng thấy", "Em thắp lên ngọn lửa"] * 4)
                           for line in pair)
    assert looks_like_translation(vietnamese)
    # a song in two languages isn't one, nor "translation" sung in a line
    assert not looks_like_translation(f"{ENGLISH}\n{russian[0]}\n{ENGLISH}\nLost in translation tonight")


@pytest.mark.parametrize("text, language, sure", [
    ("Wróc na Kaszëbë, tam gdze mòja chëcz stoji, tam gdze mòrze szumi i wiater wieje, tam jô bëc chcã do "
     "kùńca dniów", "Kashubian", True),  # ò ù ë beside a Polish reading
    ("Sıra-sıra djezveler qaynar da qaynarlar, alimenin qaşları oynar da oynarlar, sıra-sıra djezveler "
     "qaynar da qaynarlar", "Crimean Tatar", False),
    ("Quel flor qu'tita murchá, quel criancá qu'tita tchorá, quel nuvem qu'tita passá, é sodade, é tristeza, "
     "é lembranca di nha infância", "Cape Verdean Creole", False),
    ("Þú ert ljósið mitt og ég elska þig, þegar sólin sest yfir fjörðinn þá hugsa ég um þig alla nóttina, og "
     "þegar vindurinn blæs yfir hafið þá syng ég lagið okkar aftur og aftur", "Icelandic", True),  # read as Hungarian
    ("Me a gano d'ar c'hoad hag d'ar mor bras, ar c'hleze a zo war an daol, kenavo d'an holl tud vat, ha bremañ e "
     "kanomp asambles e-kreiz an noz, war an hent bras etrezek ar gêr", "Breton", True),  # read as Welsh
    ("Mwen kay pati lè solèy kouché, nou ké chanté ansanm toujou, fo pa ou pléré chéri, sé lanmou ki la pou nou",
     "Guadeloupean Creole", False),  # read as French
])
def test_languages_langdetect_lacks_come_from_their_letters_or_words(text, language, sure):
    from utils.common.language_names import language_of_text

    found = language_of_text(text)
    assert (found[0], found[1] == 1.0) == (language, sure)


def test_a_reading_langdetect_fills_with_unknown_languages_needs_its_words(monkeypatch):
    from utils.common import language_names
    from utils.common.language_names import language_of_text, song_language

    altai = ("Öt tengeri kaira elikán hulgan märgän, kizirgán umai gúwará gúpái, goya zhasgáná ayyadá gynnáná "
             "yeláná yeládá börkit")
    assert language_of_text(altai) == ("Hungarian", 0.5)  # no "és", "hogy", "nem"...: never sure
    hungarian = ("Nem tudom, hogy mit akarsz, de én itt vagyok és várok rád, mert csak te vagy az egy nekem, "
                 "és ha eljön az éjszaka, már nem félek semmitől")
    assert language_of_text(hungarian) == ("Hungarian", 1.0)
    # Serbian read as Slovenian: without "in", "kaj", "sem"... it goes by the country and the year
    monkeypatch.setattr(language_names, "language_of_text", lambda text: ("Slovenian", 1.0))
    assert song_language("Ej Ljubice, Beograd noću sija", "Ovo je Balkan", "Milan Stanković") == (
        "Serbo-Croatian", 1.0, True)
    assert song_language("Ej devojko, Beograd noću sija", "Ovo je Balkan", "Milan Stanković")[0] == "Serbian"
    assert song_language("S tabo vedno je poletje, ko pogledaš me vem, da uredu sem", "Stabo", "Skinshape")[0] \
        == "Slovenian"


def test_a_line_holding_its_translation_after_a_caret_is_read_without_it():
    from utils.common.language_names import language_of_text

    lines = ["Te quiero mucho mi amor de verdad^I really love you a lot my love",
             "Bailamos toda la noche en la ciudad^We dance all night in the city",
             "Y nunca vamos a parar de cantar^And we are never going to stop singing",
             "Las estrellas brillan sobre el mar^The stars shine over the sea",
             "Mi corazón late por ti cada día^My heart beats for you every day"]
    assert language_of_text("\n".join(lines)) == ("Spanish", 1.0)


def test_lines_count_once_and_the_stanzas_vote():
    from utils.common.language_names import language_of_text

    croatian = CROATIAN.replace(", ", "\n")
    intro = "Ha-ha, listen here, we are coming at you easy now, freedom for every child all around the world"
    assert language_of_text(f"{intro}\n\n{croatian}\n\n{croatian.upper()}\n\n{EKAVIAN}")[0] == "Croatian"
    # a few lines sung over and over: read, never sure
    found = language_of_text("\n".join(["I'm just a kid", "It was just a little mistake"] * 12))
    assert found is None or found[1] <= 0.5
    # a second language most of a stanza is in
    german = ("Du bist so schmutzig, du bist so schön, ich kann dich riechen, ich kann dich sehen, und alles was "
              "ich will ist bei dir zu sein heute Nacht")
    assert language_of_text(f"{german}\n\n{ENGLISH}\n\n{german.upper()}")[0] == "German / English"


def test_a_short_cyrillic_stanza_without_its_letters_has_no_vote():
    from utils.common.language_names import language_of_text

    belarusian = BELARUSIAN.replace(", ", "\n")
    assert language_of_text(f"{belarusian}\n\nГэта мой колер, не выходзь з дому, гэта мой колер, я твой знаёмы, "
                            f"не выходзь з дому")[0] == "Belarusian"
    # Ukrainian typed with a Latin "i"
    assert language_of_text(UKRAINIAN.replace("і", "i"))[0] == "Ukrainian"


def test_romaji_alone_and_kajkavian():
    from utils.common.language_names import song_language

    romaji = ("Machi no zawameki ga mimi ni nokoru yoru no shiruetto kimi no koe ga kikoeru kimi to futari de "
              "aruita michi")
    language, probability, _ = song_language(romaji, "Night Silhouette", "Chill City Pop")
    assert language in ("Japanese", "Finnish", "Estonian", "Italian", "Tagalog", "Indonesian", "Malay")
    assert language != "Japanese" or probability <= 0.5
    slovenian = ("Ker tu je vse tako lepo, tu nič me ne boli, Jan je nehal piti kavo in zdaj samo še spi, vse je "
                 "tako lepo pri nas doma")
    assert song_language(slovenian, "Raca", "Cinkuši", "Croatia")[:2] == ("Croatian (Kajkavian)", 0.5)
    assert song_language(slovenian, "Vest", "Koala Voice", "Slovenia")[0] == "Slovenian"


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
    assert found.never_sure  # the work's language: an instrumental cover of it has it too
    assert asked == ["recording/", "recording/r1", "recording/r2"]  # "Polska" is another song

    monkeypatch.setattr(mb, "mb_get", lambda url, params: None)
    with pytest.raises(ConnectionError):  # asked again next time, not remembered as unknown
        mb.get_language("Kult", "Arahja")


@pytest.fixture
def youtube(monkeypatch):
    """youtube_language_fetcher with its cache emptied, the key set and the
    Data API faked: `codes` = what each video names, `asked` = the requests."""
    from types import SimpleNamespace
    from utils.discoveries.discovery_modules import youtube_language_fetcher as module

    fake = SimpleNamespace(module=module, codes={}, asked=[], fails=False)

    def api(ids):
        fake.asked.append(list(ids))
        return None if fake.fails else {video_id: fake.codes.get(video_id, "") for video_id in ids}
    monkeypatch.setattr(module, "_codes", {})
    monkeypatch.setattr(module, "_failed", set())
    monkeypatch.setattr(module, "audio_languages_via_api", api)
    monkeypatch.setattr(module.Settings, "youtube_api_key", "key")
    return fake


@pytest.mark.parametrize("code, language", [
    ("pl", "Polish"), ("en-US", "English"), ("pt-BR", "Portuguese"), ("EN-gb", "English"),
    ("zxx", "Instrumental"),  # no linguistic content
    ("el", "Greek"), ("nb", "Norwegian"), ("be", "Belarusian"),
    ("und", None), ("", None), ("qq", None),
])
def test_youtube_audio_language_codes(code, language):
    from utils.discoveries.discovery_modules.youtube_language_fetcher import language_of_code
    assert language_of_code(code) == language


def test_youtube_audio_language_of_the_songs_own_video_is_never_sure(youtube):
    from types import SimpleNamespace

    youtube.codes = {"v1": "pl", "v2": "und"}
    songs = [SimpleNamespace(youtube_video_id=video_id) for video_id in ("v1", "v2", "v1", None, "", "N/A", "v3")]
    youtube.module.prepare(songs)
    assert youtube.asked == [["v1", "v2", "v3"]]  # one request for every video, none without a link

    found = youtube.module.get_language("A", "T", songs=songs[:1])
    assert (found.language, found.from_song, found.never_sure, found.source_ref) == ("Polish", True, True, "v1")
    assert [youtube.module.get_language("A", "T", songs=[song]) for song in songs[3:]] == [None] * 4
    assert youtube.module.get_language("A", "T", songs=songs[1:2]) is None  # "und" = nothing
    assert len(youtube.asked) == 1  # all answered by that request
    youtube.module.get_language("A", "T", songs=[SimpleNamespace(youtube_video_id="v4")])
    assert youtube.asked[-1] == ["v4"]  # a song prepare() didn't see is asked alone


def test_youtube_audio_language_without_the_key_or_with_the_api_down(youtube, monkeypatch):
    from types import SimpleNamespace

    song = [SimpleNamespace(youtube_video_id="v1")]
    monkeypatch.setattr(youtube.module.Settings, "youtube_api_key", None)
    youtube.module.prepare(song)
    assert youtube.module.get_language("A", "T", songs=song) is None
    assert youtube.asked == []  # quietly nothing

    monkeypatch.setattr(youtube.module.Settings, "youtube_api_key", "key")
    youtube.fails = True
    youtube.module.prepare(song)
    for _ in range(2):  # asked again next time, not remembered as having none - and not once a song
        with pytest.raises(RuntimeError):
            youtube.module.get_language("A", "T", songs=song)
    assert len(youtube.asked) == 1


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


def test_a_never_sure_source_is_unsure_and_asked_once_a_song(session):
    from utils.discoveries.discoveries_manager import discover_language
    from utils.discoveries.discovery_result import LanguageDiscoveryResult

    song = Song(title="Arahja", artist=Artist(name="Kult"))
    session.add(song)
    session.flush()
    silent = _module()
    silent.BY_SONG = True
    youtube = _module(lambda a, t: LanguageDiscoveryResult("Polish", from_song=True, never_sure=True))
    search = discover_language(song, [("silent", "S", silent), ("youtube", "Y", youtube)])
    assert (search.match.result.language, search.match.sure, search.missed) == ("Polish", False, ["silent"])
    assert len(silent.calls) == 1  # its answer doesn't depend on the names asked


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


def test_youtubes_audio_language_goes_to_the_review_after_the_sites(session, monkeypatch, youtube):
    kult, gibonni = Artist(name="Kult"), Artist(name="Gibonni", origin="Croatia - Dalmatia - Split")
    session.add_all([Song(title="Known", artist=kult, language="Polish")])
    arahja = Song(title="Arahja", artist=kult, language="Unknown", youtube_video_id="v1")
    site_knows = Song(title="Away", artist=kult, youtube_video_id="v2")
    croatian = Song(title="Zlatne godine", artist=gibonni, year=1989, youtube_video_id="v3")
    session.add_all([arahja, site_knows, croatian])
    session.flush()
    youtube.codes = {"v1": "pl", "v2": "en", "v3": "hr"}
    site = _module(lambda a, t: _result("English", title="Away", artist="Kult") if t == "Away" else None)
    _fill_with(session, monkeypatch, {}, site)
    modules = language_review.load_discovery_modules(None) + [("youtube_language_fetcher", "Y", youtube.module)]
    monkeypatch.setattr(language_review, "load_discovery_modules", lambda kind: modules)
    shown = {}

    def checkbox(_question, choices):
        shown.update({choice.title: choice.checked for choice in choices})
        return type("Prompt", (), {"ask": lambda self: []})()
    monkeypatch.setattr(questionary, "checkbox", checkbox)

    language_review.fill_missing_languages()

    assert youtube.asked == [["v3", "v1", "v2"]]  # one request before the songs are asked (by artist)
    assert site_knows.language == "English"  # the site before YouTube, and sure
    # never saved without the owner, the country-and-year rule applied to its "hr"
    assert shown == {"Kult - Arahja  ->  Polish  (youtube_language_fetcher; the artist: 1/1 songs)": True,
                     "Gibonni - Zlatne godine  ->  Serbo-Croatian  (youtube_language_fetcher)": False}
    assert (arahja.language, croatian.language) == ("Unknown", None)


# ==================== Covers (owner's rule, 2026-11-20) ====================

def _cover(session, original, artist, title, **fields):
    from utils.database import covers
    song = Song(title=title, artist=Artist(name=artist), language="Unknown", **fields)
    session.add(song)
    covers.set_covered_artist([song], original.artist)
    session.flush()
    return song


def test_a_cover_singing_the_originals_words_takes_its_language(session):
    from utils.database.languages import original_language

    colic = Song(title="Juče još", artist=Artist(name="Zdravko Čolić"), year=1978, language="Serbo-Croatian")
    toto = Song(title="Africa", artist=Artist(name="Toto"), language="English")
    hero = Song(title="Holding Out For A Hero", artist=Artist(name="Bonnie Tyler"), language="English")
    session.add_all([colic, toto, hero])
    session.flush()
    ceka = _cover(session, colic, "Ceka", "Juce jos", year=2012)
    africa = _cover(session, toto, "UkrTrashDub", "Африка", covered_title="Africa")
    vitr = _cover(session, hero, "Lucie Vondráčková", "Vítr", covered_title="Holding Out for a Hero")
    marked = _cover(session, toto, "Someone", "Africa", cover_translated=1)
    assert original_language(ceka) == "Serbo-Croatian"
    # not marked yet, but its title is in another script / not the original's
    assert original_language(africa) is original_language(vitr) is None
    assert original_language(marked) is None


def test_the_yugoslav_rule_goes_by_the_originals_year_and_artist(session):
    from utils.database.languages import words_basis

    colic = Song(title="Juče još", artist=Artist(name="Zdravko Čolić", origin="Serbia"), year=1978)
    other = Song(title="Druga", artist=colic.artist, language="Serbo-Croatian")
    session.add_all([colic, other])
    session.flush()
    ceka = _cover(session, colic, "Ceka", "Juce jos", year=2012)
    assert words_basis(ceka) == (1978, "Serbia", ["Serbo-Croatian"])
    ceka.cover_translated = 1
    assert words_basis(ceka) == (2012, None, [])


def test_fill_missing_languages_saves_the_originals_language_first(session, monkeypatch):
    colic = Song(title="Juče još", artist=Artist(name="Zdravko Čolić"), year=1978, language="Serbo-Croatian")
    session.add(colic)
    session.flush()
    ceka = _cover(session, colic, "Ceka", "Juce jos", year=2012)
    lyrics = _module(lambda a, t: _result("Croatian", 1.0, from_song=True), local=True)
    _fill_with(session, monkeypatch, {})
    monkeypatch.setattr(language_review, "load_discovery_modules", lambda kind: [("stored_lyrics_fetcher", "L", lyrics)])

    assert language_review.fill_missing_languages() == 1
    assert ceka.language == "Serbo-Croatian" and not lyrics.calls  # no module asked
