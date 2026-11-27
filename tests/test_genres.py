"""A song's genres (songs.genre, migration 0013; utils/database/genres.py):
one spelling per genre (MusicBrainz's list, synonyms, the spellings music.db
already uses), merges keeping a genre, the MusicBrainz lookup for Fill
missing data -> Genres (music_brainz_fetcher.get_genre(), its requests faked)
- song tags, then the album's release group, then the artist -, the other
get_genre() modules (Wikipedia's infobox, iTunes' store genre, Google's panel)
and discoveries_manager.discover_genre() choosing among them.
In-memory SQLite, no mocking of SQL."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from config.constants import GENRE_LEVEL_ALBUM, GENRE_LEVEL_ARTIST, GENRE_LEVEL_SONG
from utils.database import genres as gn
from utils.database.datatables import Artist, Base, Song
from utils.discoveries import discoveries_manager as dm
from utils.discoveries.discovery_modules import google_search_fetcher as gsf
from utils.discoveries.discovery_modules import itunes_fetcher as itf
from utils.discoveries.discovery_modules import lastfm_fetcher as lfm
from utils.discoveries.discovery_modules import music_brainz_fetcher as gl
from utils.discoveries.discovery_modules import wikipedia_fetcher as wf
from utils.discoveries.discovery_result import GenreDiscoveryResult


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


# ==================== spelling ====================

@pytest.mark.parametrize("typed, stored", [
    ("Synthpop", "synth-pop"),
    ("synth pop", "synth-pop"),
    ("Hip-Hop", "hip hop"),
    ("RnB", "r&b"),
    ("Rhythm & Blues", "r&b"),
    ("drum'n'bass", "drum and bass"),
    ("Rock & Roll", "rock and roll"),
    ("K-Pop", "k-pop"),
    ("Szanty  Morskie", "szanty morskie"),  # unknown: lowercased as typed
])
def test_one_spelling_per_genre(typed, stored):
    assert gn.canonical_genre(typed) == stored


def test_clean_genres_keeps_order_and_drops_repeats():
    assert gn.clean_genres(" Indie Pop , synthpop; indie-pop,, Synth-Pop ") == "indie pop, synth-pop"
    assert gn.clean_genres("  ") is None
    assert gn.clean_genres(None) is None


def test_the_databases_spelling_wins_over_musicbrainz(session):
    artist = Artist(name="A")
    session.add_all([Song(title="1", artist=artist, genre="Synth Pop, poezja śpiewana"),
                     Song(title="2", artist=artist, genre="Synth Pop")])
    song = Song(title="3", artist=artist)
    session.add(song)
    session.flush()

    old = gn.set_song_genre(session, song, "synthpop, Poezja Spiewana, pop")

    assert old is None
    assert song.genre == "Synth Pop, poezja śpiewana, pop"


def test_one_song_may_respell_a_genre_only_it_has(session):
    song = Song(title="1", artist=Artist(name="A"), genre="Synth Pop")
    session.add(song)
    session.flush()

    gn.set_song_genre(session, song, "synthpop")

    assert song.genre == "synth-pop"


def test_merge_keeps_a_genre(session):
    keep, drop = Song(title="x", genre=None), Song(title="x", genre="pop")
    gn.keep_genre_on_merge(keep, drop)
    assert keep.genre == "pop"
    keep.genre, drop.genre = "rock", "pop"
    gn.keep_genre_on_merge(keep, drop)
    assert keep.genre == "rock"


def test_artist_genre_proposal():
    counts = gn.Counter({"": 4, "rock": 4, "punk": 2, "ska": 1})
    assert gn.artist_genre_proposal(counts) == ("rock, punk", 1.0, 4)  # ska: under half the songs
    assert gn.artist_genre_proposal(None) is None
    assert gn.artist_genre_proposal(gn.Counter({"": 3, "a": 1, "b": 1, "c": 1})) is None


def test_artist_genres_count_each_songs_genres_once(session):
    artist = Artist(name="Dżem")
    session.add_all([Song(title="A", artist=artist, genre="blues rock, rock"),
                     Song(title="B", artist=artist, genre="blues rock"), Song(title="C", artist=artist)])
    session.commit()
    assert gn.artist_genres(session)[artist.id] == gn.Counter({"": 2, "blues rock": 2, "rock": 1})


def test_songs_missing_genre(session):
    artist = Artist(name="A")
    session.add_all([Song(title="b", artist=artist), Song(title="a", artist=artist, genre=""),
                     Song(title="c", artist=artist, genre="pop")])
    session.flush()

    assert [s.title for s in gn.songs_missing_genre(session)] == ["a", "b"]


# ==================== MusicBrainz lookup ====================

def _song(title="Enjoy the Silence", artist="Depeche Mode", album="Violator", synonyms=None):
    return SimpleNamespace(title=title, title_latin=None, album=album, other_titles=None, covered_artist=None,
                           additional_artists=[],
                           artist=SimpleNamespace(name=artist, synonyms=synonyms, name_latin=None))


def _recording(title="Enjoy the Silence", artist="Depeche Mode", tags=(), groups=(), **kw):
    return {"id": f"rec-{title}", "title": title, "tags": [{"name": n, "count": c} for n, c in tags],
            "artist-credit": [{"name": artist, "artist": {"id": "art-1", "name": artist}}],
            "releases": [{"title": g.get("title"), "release-group": g} for g in groups], **kw}


@pytest.fixture
def mb(monkeypatch):
    """Answers keyed by URL ending; records what was asked."""
    answers, asked = {}, []

    def fake(url, params):
        asked.append((url, params))
        for key, value in answers.items():
            if url.endswith(key):
                return value(params) if callable(value) else value
        return {}

    monkeypatch.setattr(gl, "_mb_json", fake)
    return SimpleNamespace(answers=answers, asked=asked)


def test_song_tags_that_are_genres_win(mb):
    mb.answers["recording/"] = {"recordings": [
        _recording(tags=[("synth-pop", 5), ("new wave", 3), ("seen live", 9), ("electronic", 1)]),
        _recording(tags=[("Synthpop", 2)]),
        _recording(title="Policy of Truth", tags=[("rock", 50)]),
        _recording(tags=[("rock", 50)], disambiguation="live, 1990"),
    ]}

    result = gl.GenreLookup().lookup(_song())

    # "seen live" isn't a genre, "electronic" is under 30 % of the top's votes
    assert (result.genres, result.level, result.checked) == (["synth-pop", "new wave"], GENRE_LEVEL_SONG, True)
    assert 'release:"Violator"' in mb.asked[0][1]["query"]


def test_then_the_albums_release_group(mb):
    mb.answers["recording/"] = {"recordings": [_recording(groups=[
        {"id": "rg-single", "title": "Enjoy the Silence", "primary-type": "Single"},
        {"id": "rg-live", "title": "Violator", "primary-type": "Album", "secondary-types": ["Live"]},
        {"id": "rg-album", "title": "Violator", "primary-type": "Album"},
    ])]}
    mb.answers["release-group/rg-album"] = {"genres": [{"name": "synth-pop", "count": 4}]}

    result = gl.GenreLookup().lookup(_song())

    assert (result.genres, result.level) == (["synth-pop"], GENRE_LEVEL_ALBUM)


def test_then_the_artist_once_per_run(mb):
    mb.answers["recording/"] = {"recordings": [_recording()]}
    mb.answers["artist/art-1"] = {"genres": [{"name": "electronic", "count": 28}, {"name": "synth-pop", "count": 25}]}
    lookup = gl.GenreLookup()

    first = lookup.lookup(_song())
    lookup.lookup(_song())

    assert (first.genres, first.level) == (["electronic", "synth-pop"], GENRE_LEVEL_ARTIST)
    assert sum(url.endswith("artist/art-1") for url, _ in mb.asked) == 1


def test_artist_search_when_no_recording_matches(mb):
    mb.answers["recording/"] = {"recordings": [_recording(artist="Someone Else")]}
    mb.answers["artist/"] = {"artists": [{"id": "art-9", "name": "Кино"}]}
    mb.answers["artist/art-9"] = {"genres": [{"name": "post-punk", "count": 3}]}

    result = gl.GenreLookup().lookup(_song(title="Группа крови", artist="Kino", album="Singles", synonyms="Кино"))

    assert (result.genres, result.level) == (["post-punk"], GENRE_LEVEL_ARTIST)


def test_nothing_found(mb):
    assert gl.GenreLookup().lookup(_song()) is None


def test_musicbrainz_not_answering_raises_so_the_song_is_asked_again(monkeypatch):
    monkeypatch.setattr(gl, "mb_get", lambda url, params: None)
    with pytest.raises(ConnectionError):
        gl.GenreLookup().lookup(_song())


def test_get_genre_asks_about_the_song_itself():
    assert gl.get_genre("x", "y") is None  # no song given
    assert gl.GENRE_BY_SONG and not getattr(gl, "GENRE_NEVER_SURE", False)


# ==================== the other modules ====================

_WIKI_PAGE = """<table class="infobox"><tr><th class="infobox-header">Single by Depeche Mode</th></tr>
<tr><th class="infobox-label">Genre</th><td class="infobox-data"><a>Synth-pop</a><sup><a>[1]</a></sup>
<a>new wave</a></td></tr></table>"""


@pytest.fixture
def wiki(monkeypatch):
    page = SimpleNamespace(title="Enjoy the Silence", url="https://en.wikipedia.org/wiki/Enjoy_the_Silence")
    monkeypatch.setattr(wf, "_fetch_wikipedia_page", lambda artist, title, strict=False: page)
    monkeypatch.setattr(wf, "_fetch_soup", lambda url, strict=False: wf.BeautifulSoup(_WIKI_PAGE, "html.parser"))
    return page


def test_wikipedia_reads_the_songs_infobox(wiki):
    result = wf.get_genre("Depeche Mode", "Enjoy the Silence")
    assert (result.genres, result.matched_artist, result.never_sure) == (["Synth-pop", "new wave"], "Depeche Mode", True)


def test_wikipedia_search_failing_raises(monkeypatch):
    def down(query):
        raise wf.requests.exceptions.ConnectionError("no network")
    monkeypatch.setattr(wf.wikipedia, "search", down)
    monkeypatch.setattr(wf.time, "sleep", lambda s: None)
    with pytest.raises(wf.requests.exceptions.ConnectionError):
        wf.get_genre("Depeche Mode", "Enjoy the Silence")
    assert wf._fetch_wikipedia_page("Depeche Mode", "Enjoy the Silence") is None  # the album lookup carries on


def test_wikipedia_never_takes_another_page(wiki):
    wiki.title = "Violator (album)"
    assert wf.get_genre("Depeche Mode", "Enjoy the Silence") is None


def _itunes(*tracks):
    return lambda artist, title: [{"kind": "song", "artistName": a, "trackName": t, "primaryGenreName": g}
                                  for a, t, g in tracks]


def test_itunes_store_genre_of_the_artists_track(monkeypatch):
    monkeypatch.setattr(itf, "_search", _itunes(("Someone", "Enjoy the Silence", "Pop"),
                                                ("Depeche Mode", "Enjoy the Silence (Live)", "Rock"),
                                                ("Depeche Mode", "Enjoy the Silence", "Alternative")))
    result = itf.get_genre("Depeche Mode", "Enjoy the Silence")
    assert (result.genres, result.matched_artist) == (["alternative rock"], "Depeche Mode")


def test_itunes_artist_matched_with_letters_folded(monkeypatch):
    monkeypatch.setattr(itf, "_search", _itunes(("Organek", "Mississippi w Ogniu", "Rock")))
    assert itf.get_genre("Ørganek", "Mississippi w ogniu").genres == ["Rock"]


def test_itunes_genre_split_at_slash(monkeypatch):
    monkeypatch.setattr(itf, "_search", _itunes(("Bob", "Song", "Electronic/Dance")))
    assert itf.get_genre("Bob", "Song").genres == ["Electronic", "Dance"]


def test_google_blocked_raises_and_its_genres_are_split(monkeypatch):
    def panel(artist, query):
        gsf.blocked = True
    monkeypatch.setattr(gsf, "_fetch_knowledge_panel_info", panel)
    with pytest.raises(ConnectionError):
        gsf.get_genre("Bob", "Song")
    monkeypatch.setattr(gsf, "blocked", False)
    monkeypatch.setattr(gsf, "_fetch_knowledge_panel_info",
                        lambda artist, query: {"genre": "Pop, Alternative/Indie", "artists": "Bob"})
    assert gsf.get_genre("Bob", "Song").genres == ["Pop", "Alternative", "Indie"]


class _LastFm:
    """Last.fm's answers by method; a missing one is error 6, not found."""

    def __init__(self, monkeypatch, **answers):
        self.asked = []
        monkeypatch.setattr(lfm.settings, "lastfm_api_key", "key")
        monkeypatch.setattr(lfm._pacer, "wait", lambda: None)
        monkeypatch.setattr(lfm.requests, "get", self.get)
        self.answers = answers

    def get(self, url, params, timeout):
        self.asked.append(params["method"])
        answer = self.answers.get(params["method"].split(".")[0], {"error": 6, "message": "not found"})
        return SimpleNamespace(json=lambda: answer, raise_for_status=lambda: None)


def _tags(*tags, **names):
    return {"toptags": {"tag": [{"name": n, "count": c} for n, c in tags], "@attr": names}}


def test_lastfm_takes_the_tracks_weighty_tags(monkeypatch):
    _LastFm(monkeypatch, track=_tags(("shoegaze", 100), ("dream pop", 40), ("seen live", 5),
                                     artist="Slowdive", track="Alison"))
    result = lfm.get_genre("slowdive", "alison")
    assert (result.genres, result.matched_title, result.matched_artist) == (["shoegaze", "dream pop"], "Alison", "Slowdive")
    assert result.level == GENRE_LEVEL_SONG and result.never_sure


def test_lastfm_falls_back_to_the_artist(monkeypatch):
    fake = _LastFm(monkeypatch, artist=_tags(("post-punk", 100), artist="Молчат Дома"))
    result = lfm.get_genre("Молчат Дома", "Судно")
    assert fake.asked == ["track.getTopTags", "artist.getTopTags"]
    assert (result.genres, result.level, result.matched_title) == (["post-punk"], GENRE_LEVEL_ARTIST, None)


def test_lastfm_unknown_is_none_but_failures_and_no_key_raise(monkeypatch):
    _LastFm(monkeypatch)
    assert lfm.get_genre("Nobody", "Nothing") is None
    _LastFm(monkeypatch, track={"error": 29, "message": "Rate limit exceeded"})
    with pytest.raises(ConnectionError):
        lfm.get_genre("Bob", "Song")
    monkeypatch.setattr(lfm.settings, "lastfm_api_key", "")
    monkeypatch.setattr(lfm, "warn", lambda message: None)
    with pytest.raises(ConnectionError):
        lfm.get_genre("Bob", "Song")


# ==================== discover_genre() ====================

def _module(result=None, error=None, never_sure=False, by_song=False):
    asked = []

    def get_genre(artist, title, songs=None):
        asked.append((artist, title))
        if error:
            raise error
        return result
    return SimpleNamespace(get_genre=get_genre, asked=asked, GENRE_NEVER_SURE=never_sure, GENRE_BY_SONG=by_song)


def _discover(song, **modules):
    return dm.discover_genre(song, [(name, name, module) for name, module in modules.items()])


def test_a_sure_song_genre_wins_and_stops(monkeypatch):
    monkeypatch.setattr(dm, "_lyrics_queries", lambda song: [("Depeche Mode", "Enjoy the Silence")])
    mb = _module(GenreDiscoveryResult(["Synthpop", "seen live"], checked=True))
    later = _module(GenreDiscoveryResult(["rock"], matched_artist="Depeche Mode"))
    search = _discover(_song(), mb=mb, later=later)
    assert (search.match.genres, search.match.sure, later.asked) == ("synth-pop", True, [])


def test_an_unsure_one_is_kept_and_never_sure_modules_are_skipped_after_it(monkeypatch):
    monkeypatch.setattr(dm, "_lyrics_queries", lambda song: [("Depeche Mode", "Enjoy the Silence")])
    mb = _module(GenreDiscoveryResult(["electronic"], GENRE_LEVEL_ARTIST, checked=True))
    wiki = _module(GenreDiscoveryResult(["synth-pop"], matched_artist="Depeche Mode", never_sure=True), never_sure=True)
    search = _discover(_song(), mb=mb, wiki=wiki)
    assert (search.match.module_id, search.match.sure, wiki.asked) == ("mb", False, [])


def test_misses_failures_and_other_songs(monkeypatch):
    monkeypatch.setattr(dm, "_lyrics_queries", lambda song: [("Depeche Mode", "Enjoy the Silence"),
                                                             ("DM", "Enjoy the Silence"), ("x", "third")])
    other = _module(GenreDiscoveryResult(["rock"], matched_artist="Metallica"))
    unknown = _module(GenreDiscoveryResult(["Soundtrack"], matched_artist="Depeche Mode"))
    down = _module(error=ConnectionError("no network"))
    search = _discover(_song(), other=other, unknown=unknown, down=down)
    assert search.match is None
    assert (search.missed, search.failed) == (["other", "unknown"], ["down"])
    assert len(other.asked) == 2 and len(down.asked) == 1  # GENRE_MAX_QUERIES, a failure stops


def test_never_sure_alone_is_kept_for_review(monkeypatch):
    monkeypatch.setattr(dm, "_lyrics_queries", lambda song: [("Depeche Mode", "Enjoy the Silence")])
    itunes = _module(GenreDiscoveryResult(["Alternative Rock"], matched_title="Enjoy the Silence",
                                          matched_artist="Depeche Mode", never_sure=True), never_sure=True)
    search = _discover(_song(), itunes=itunes)
    assert (search.match.genres, search.match.sure) == ("alternative rock", False)


# ==================== migration ====================

def test_migration_adds_the_column(tmp_path):
    import sqlite3
    sql = (repo_root / "migrations" / "music" / "0013_song_genre.sql").read_text()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE songs (id INTEGER PRIMARY KEY, title VARCHAR)")
    conn.executescript(sql)
    assert "genre" in [row[1] for row in conn.execute("PRAGMA table_info(songs)")]
