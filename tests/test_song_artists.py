"""Multi-artist songs: utils/database/song_artists.py helpers, the DB search
filters that reach additional artists, and artist merges carrying
additional_song_artists rows along. In-memory SQLite, no mocking of SQL."""

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database.datatables import AdditionalSongArtist, Artist, Base, Song
from utils.database.database_getter import SongSearchFilters
from utils.database import song_artists as sa

# See test_resolve_duplicates.py for why this needs importlib
rd = importlib.import_module("menu.main_menu.enter_database.manage_database.resolve_duplicates")


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def session(session_factory):
    s = session_factory()
    yield s
    s.close()


def _artists(session, *names):
    artists = [Artist(name=n) for n in names]
    session.add_all(artists)
    session.flush()
    return artists


def _song(session, title, artist):
    song = Song(title=title, artist=artist)
    session.add(song)
    session.flush()
    return song


def _titles(session, criterion):
    return sorted(s.title for s in session.query(Song).join(Artist).filter(criterion).all())


# ---- labels -----------------------------------------------------------------

def test_label_of_single_artist_song_is_just_the_artist_name(session):
    (a,) = _artists(session, "Sw@da")
    song = _song(session, "Solo", a)
    assert sa.song_artist_label(song) == "Sw@da"
    assert sa.song_all_artists(song) == [a]


def test_label_lists_main_artists_then_feats_with_extras_sorted_by_name(session):
    primary, niczos, maxim, guest = _artists(session, "Sw@da", "Niczos", "Maxim", "Guest")
    song = _song(session, "Collab", primary)
    sa.add_additional_artist(song, guest, "feat")
    sa.add_additional_artist(song, niczos, "main")
    sa.add_additional_artist(song, maxim, "main")
    session.flush()

    assert sa.song_artist_label(song) == "Sw@da x Maxim x Niczos feat. Guest"
    assert sa.song_main_artists(song) == [primary, maxim, niczos]
    assert sa.song_feat_artists(song) == [guest]


# ---- add / remove / role ----------------------------------------------------

def test_add_rejects_primary_artist_duplicate_link_and_unknown_role(session):
    primary, other = _artists(session, "A", "B")
    song = _song(session, "S", primary)

    with pytest.raises(ValueError):
        sa.add_additional_artist(song, primary, "main")
    with pytest.raises(ValueError):
        sa.add_additional_artist(song, other, "guest")

    sa.add_additional_artist(song, other, "feat")
    with pytest.raises(ValueError):
        sa.add_additional_artist(song, other, "main")
    session.flush()
    assert session.query(AdditionalSongArtist).count() == 1


def test_remove_and_change_role(session):
    primary, other = _artists(session, "A", "B")
    song = _song(session, "S", primary)
    sa.add_additional_artist(song, other, "main")
    session.flush()

    sa.set_additional_artist_role(song, other, "feat")
    session.flush()
    assert sa.additional_artist_role(song, other) == "feat"
    assert sa.song_artist_label(song) == "A feat. B"

    sa.remove_additional_artist(song, other)
    session.flush()
    assert session.query(AdditionalSongArtist).count() == 0
    assert session.get(Artist, other.id) is not None  # the artist row itself stays
    with pytest.raises(ValueError):
        sa.remove_additional_artist(song, other)


def test_db_rejects_unknown_role(session):
    primary, other = _artists(session, "A", "B")
    song = _song(session, "S", primary)
    session.add(AdditionalSongArtist(song_id=song.id, artist_id=other.id, role="guest"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_deleting_song_removes_its_links(session):
    primary, other = _artists(session, "A", "B")
    song = _song(session, "S", primary)
    sa.add_additional_artist(song, other, "main")
    session.flush()

    session.delete(song)
    session.flush()
    assert session.query(AdditionalSongArtist).count() == 0


# ---- search -----------------------------------------------------------------

@pytest.fixture
def searchable(session):
    swada, maxim, niczos, unrelated = _artists(session, "Sw@da", "Maxim", "Niczos", "Unrelated")
    collab = _song(session, "Collab", swada)
    sa.add_additional_artist(collab, maxim, "main")
    sa.add_additional_artist(collab, niczos, "feat")
    _song(session, "Maxim Solo", maxim)
    _song(session, "Other", unrelated)
    session.flush()
    return {"maxim": maxim, "niczos": niczos, "unrelated": unrelated}


def test_artist_name_search_finds_songs_where_artist_is_additional(session, searchable):
    assert _titles(session, SongSearchFilters.artist_name("maxim")) == ["Collab", "Maxim Solo"]
    assert _titles(session, SongSearchFilters.artist_name("niczos")) == ["Collab"]
    assert _titles(session, SongSearchFilters.artist_name("unrelated")) == ["Other"]


def test_general_search_reaches_additional_artists(session, searchable):
    assert _titles(session, SongSearchFilters.general_search("niczos")) == ["Collab"]


def test_artist_id_search_and_count_include_additional(session, searchable):
    maxim_id = searchable["maxim"].id
    assert _titles(session, SongSearchFilters.artist_id(str(maxim_id))) == ["Collab", "Maxim Solo"]
    assert sa.count_artist_songs(session, maxim_id) == 2
    assert sa.count_artist_songs(session, searchable["niczos"].id) == 1


# ---- artist merges ----------------------------------------------------------

def test_reassign_moves_links_and_drops_ones_that_would_duplicate(session):
    primary, dup, keep, other = _artists(session, "P", "Dup", "Keep", "Other")
    s1 = _song(session, "Only dup", primary)        # dup only -> moved to keep
    s2 = _song(session, "Both", primary)            # dup and keep -> dup link dropped
    s3 = _song(session, "Keep is primary", keep)    # dup extra, keep primary -> dropped
    sa.add_additional_artist(s1, dup, "feat")
    sa.add_additional_artist(s2, dup, "feat")
    sa.add_additional_artist(s2, keep, "main")
    sa.add_additional_artist(s3, dup, "main")
    sa.add_additional_artist(s3, other, "main")
    session.flush()

    sa.reassign_additional_artist_links(session, dup.id, keep.id)

    rows = sorted((r.song_id, r.artist_id, r.role) for r in session.query(AdditionalSongArtist).all())
    assert rows == sorted([
        (s1.id, keep.id, "feat"),
        (s2.id, keep.id, "main"),
        (s3.id, other.id, "main"),
    ])
    # cached collections were expired, so the ORM sees the raw-SQL result
    assert sa.song_artist_label(s1) == "P feat. Keep"


def test_reassign_drops_link_when_song_primary_became_the_target(session):
    dup, keep = _artists(session, "Dup", "Keep")
    song = _song(session, "S", dup)
    sa.add_additional_artist(song, keep, "main")
    session.commit()

    session.execute(
        Song.__table__.update().where(Song.artist_id == dup.id).values(artist_id=keep.id)
    )
    sa.reassign_additional_artist_links(session, dup.id, keep.id)
    assert session.query(AdditionalSongArtist).count() == 0


def test_remove_duplicate_artists_carries_additional_links_over(session_factory, monkeypatch):
    monkeypatch.setattr(rd, "get_music_session", lambda: session_factory())
    session = session_factory()
    primary, original, duplicate = _artists(session, "Primary", "Maxim", "maxim")
    song = _song(session, "Collab", primary)
    sa.add_additional_artist(song, duplicate, "main")
    session.commit()
    original_id, duplicate_id, song_id = original.id, duplicate.id, song.id
    session.close()

    rd.remove_duplicate_artists()

    session = session_factory()
    assert session.get(Artist, duplicate_id) is None
    rows = [(r.song_id, r.artist_id) for r in session.query(AdditionalSongArtist).all()]
    assert rows == [(song_id, original_id)]
    session.close()


# ---- migration --------------------------------------------------------------

def test_migration_creates_table_matching_the_model_and_is_rerunnable():
    migrations = repo_root / "migrations" / "music"
    conn = sqlite3.connect(":memory:")
    conn.executescript((migrations / "0001_baseline.sql").read_text())
    sql = (migrations / "0003_additional_song_artists.sql").read_text()
    conn.executescript(sql)
    conn.executescript(sql)  # create_all() may have created it first

    columns = {row[1] for row in conn.execute("PRAGMA table_info(additional_song_artists)")}
    assert columns == {c.name for c in AdditionalSongArtist.__table__.columns}

    conn.execute("INSERT INTO artists (id, name) VALUES (1, 'A'), (2, 'B')")
    conn.execute("INSERT INTO songs (id, title, artist_id) VALUES (1, 'S', 1)")
    conn.execute("INSERT INTO additional_song_artists VALUES (1, 2, 'feat')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO additional_song_artists VALUES (1, 2, 'main')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO additional_song_artists VALUES (1, 1, 'guest')")


# ---- album / year fetchers --------------------------------------------------

from types import SimpleNamespace

from utils.discoveries import discoveries_manager as dm
from utils.discoveries.discovery_result import DiscoveryResult
from utils.youtube import manage_youtube_playlists as yt
from utils.youtube import yt_cache


def _transient_collab(feat=False):
    """Sw@da x Maxim (feat. Guest) - Bahato, built without a session."""
    swada, maxim, guest = Artist(name="Sw@da"), Artist(name="Maxim", synonyms="Maksym"), Artist(name="Guest")
    song = Song(title="Bahato", artist=swada)
    sa.add_additional_artist(song, maxim, "main")
    if feat:
        sa.add_additional_artist(song, guest, "feat")
    return song


@pytest.fixture
def no_discovery_stats(monkeypatch):
    for name in ("record_invocation", "record_success", "record_year_invocation", "record_year_success"):
        monkeypatch.setattr(dm, name, lambda module_id: None)


def test_matched_artist_crediting_the_whole_collab_is_accepted_only_for_multi_artist_songs():
    result = DiscoveryResult(album="Kosmos", matched_title="Bahato", matched_artist="Sw@da, Maxim & Niczos")
    credited = ["Sw@da", "Maxim", "Niczos"]
    assert dm._validate_result(result, "Sw@da", "Bahato", "Fake", credited) == "Kosmos"
    assert dm._validate_result(result, "Sw@da", "Bahato", "Fake") is None

    unrelated = DiscoveryResult(album="Kosmos", matched_title="Bahato", matched_artist="Someone Else")
    assert dm._validate_result(unrelated, "Sw@da", "Bahato", "Fake", credited) is None


def test_album_discovery_queries_primary_artist_first_then_main_artist_label(no_discovery_stats):
    queries = []

    def fake_get_album_name(artist, title):
        queries.append(artist)
        return None

    modules = [("fake", "Fake", SimpleNamespace(get_album_name=fake_get_album_name))]
    dm.discover_album_name(_transient_collab(feat=True), modules)

    assert queries[0] == "Sw@da"
    assert queries[-1] == "Sw@da x Maxim"  # feat artists stay out of the query


def test_single_artist_album_discovery_has_no_label_fallback(no_discovery_stats):
    queries = []
    song = Song(title="Solo", artist=Artist(name="Sw@da"))
    modules = [("fake", "Fake", SimpleNamespace(get_album_name=lambda a, t: queries.append(a)))]
    dm.discover_album_name(song, modules)
    assert queries == ["Sw@da"]


def test_single_release_year_uses_credits_and_label_fallback(no_discovery_stats):
    queries = []

    def fake_get_release_year(artist, query, is_single):
        queries.append(artist)
        return 2020 if artist == "Sw@da x Maxim" else None

    modules = [("fake", "Fake", SimpleNamespace(get_release_year=fake_get_release_year))]
    year = dm.discover_release_year("Sw@da", "Bahato", True, modules, song=_transient_collab())
    assert year == 2020
    assert queries[0] == "Sw@da"


# ---- YouTube matching -------------------------------------------------------

MAINS = [("Sw@da", None), ("Maxim", "Maksym")]


def _artist_relevance(candidate_title, channel="", **kwargs):
    return yt.score_result(candidate_title, "Sw@da x Maxim", "Bahato", channel, **kwargs)[1]


def test_all_main_artists_present_in_any_order_scores_full_artist_relevance():
    assert _artist_relevance("Maxim - Bahato (ft. Sw@da)", main_artists=MAINS) == 1.0
    # a co-artist's synonym counts as that artist
    assert _artist_relevance("Sw@da & Maksym - Bahato", main_artists=MAINS) == 1.0


def test_solo_upload_by_one_main_artist_doesnt_score_full_relevance():
    assert _artist_relevance("Sw@da - Bahato", main_artists=MAINS) < 1.0


def test_explicit_credits_disable_the_regex_name_split():
    # Without credits, "Final Fantasy X OST" is split on "X" and every piece
    # is found; with credits saying it's one artist, it isn't.
    title, artist = "Final Fantasy OST - To Zanarkand", "Final Fantasy X OST"
    guessed = yt.score_result(title, artist, "To Zanarkand")[1]
    explicit = yt.score_result(
        title, artist, "To Zanarkand",
        main_artists=[(artist, None)], feat_artists=[("Guest", None)],
    )[1]
    assert guessed == 1.0
    assert explicit < 1.0


def test_featured_artist_is_a_quality_bonus_not_a_requirement():
    kwargs = {"main_artists": MAINS, "feat_artists": [("Guest", None)]}
    with_feat = yt.score_result("Sw@da x Maxim - Bahato (feat. Guest)", "Sw@da x Maxim", "Bahato", **kwargs)
    without_feat = yt.score_result("Sw@da x Maxim - Bahato", "Sw@da x Maxim", "Bahato", **kwargs)
    assert with_feat[1] == without_feat[1] == 1.0
    assert with_feat[2] - without_feat[2] == pytest.approx(yt.FEAT_ARTIST_BONUS)


def test_credit_kwargs_round_trip_from_song():
    credits = sa.song_artist_credits(_transient_collab(feat=True))
    assert credits == {"main": [["Sw@da", None], ["Maxim", "Maksym"]], "feat": [["Guest", None]]}
    assert yt._credit_kwargs(credits) == {
        "main_artists": [("Sw@da", None), ("Maxim", "Maksym")],
        "feat_artists": [("Guest", None)],
    }
    assert sa.song_artist_credits(Song(title="Solo", artist=Artist(name="A"))) is None
    assert yt._credit_kwargs(None) == {}


def test_playlist_cache_entry_keeps_primary_key_but_queries_with_label(monkeypatch):
    monkeypatch.setattr(yt_cache, "save_cache", lambda cache: None)
    song = _transient_collab(feat=True)
    cache = yt_cache.init_cache("pl", "Playlist", [song])

    entry = cache["songs"][yt_cache.make_song_key("Sw@da", "Bahato")]
    assert entry["artist"] == "Sw@da x Maxim"
    assert entry["credits"] == sa.song_artist_credits(song)
