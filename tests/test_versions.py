"""Versions (songs.version_kind / version_label / version_of, migration 0022;
utils/common/version_names.py, utils/database/versions.py)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import menu.main_menu  # noqa: F401 - import order, see AGENTS.md
from utils.common.cover_names import title_with_cover
from utils.common.version_names import read_version, title_with_version
from utils.database import song_references as sr
from utils.database import versions
from utils.database.datatables import Artist, Base, Song
from utils.database.song_duplicates import find_duplicate_groups, merge_songs
from utils.database.song_kinds import songs_for_entry
from utils.discoveries.import_engine import create_song_entry, find_existing_song, find_song_loosely


@pytest.mark.parametrize("title, expected", [
    ("Boję się (Live)", ("Boję się", "live", None)),
    ("Werewolf (live)", ("Werewolf", "live", None)),
    ("Recover (Cid Rim Remix)", ("Recover", "remix", "Cid Rim Remix")),
    ("Matylda (na żywo, akustycznie)", ("Matylda", "live", "na żywo, akustycznie")),
    ("Spider and the Fly (acoustic)", ("Spider and the Fly", "acoustic", None)),
    ("Engel (extended version)", ("Engel", "extended", "extended version")),
    ("Moon Over The Castle [Extended Orchestral Version]", ("Moon Over The Castle", "extended",
                                                           "Extended Orchestral Version")),
    ("I'm Gonna Change (Demo)", ("I'm Gonna Change", "demo", None)),
    ("Crazy in Love (URM version)", ("Crazy in Love", "other", "URM version")),
    ("Song - Acoustic Version", ("Song", "acoustic", "Acoustic Version")),
    ("Song (Live) (Remastered 2011)", ("Song (Remastered 2011)", "live", None)),
    # A contest's or a radio's performance, the shortest dashed suffix naming one
    ("I Love Belarus - Eurovision Song Contest 2011 - BBC One",
     ("I Love Belarus", "live", "Eurovision Song Contest 2011 - BBC One")),
    ("Crazy In Love (BBC Session)", ("Crazy In Love", "live", "BBC Session")),
    ("Lose Control - The Village Sessions", ("Lose Control", "live", "The Village Sessions")),
    ("Song - Part 2 - Live", ("Song - Part 2", "live", None)),
])
def test_read_version(title, expected):
    reading = read_version(title)
    assert (reading.title, reading.kind, reading.label) == expected


@pytest.mark.parametrize("title", [
    "Dziwny jest ten świat (Bonus track)", "Song (Radio Edit)", "Song (Album Version)", "Song (2015 Remastered)",
    "Town Theme 3 (Original version)", "Teenage Dirtbag - Explicit Album Version", "Twinz (Deep Cover ’98)",
    "Mix Tape", "Rasputin (Boney M cover - acoustic / live)", "Song (feat. Live)", "(Live)", None,
    # "Radio Version" names the release: nothing left of it is read as a marker
    "Dreamland - One and One - Radio Version",
])
def test_not_a_version(title):
    assert read_version(title) is None


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    sr.register_song_reference_sync(factory)
    with factory() as db:
        yield db


def _song(session, artist, title, **fields):
    row = session.query(Artist).filter_by(name=artist).first() or Artist(name=artist)
    song = Song(title=title, artist=row, **fields)
    session.add(song)
    session.flush()
    return song


def test_proposal_marks_the_version_of_its_original(session):
    original = _song(session, "Nosowska", "Boję się")
    live = _song(session, "Nosowska", "Boję się (Live)")
    remix = _song(session, "Husky", "Iskrzy Iskrzy! (Agim Remix)")
    proposals = versions.find_versions(session)
    assert [(p.song, p.original, p.certain) for p in proposals] == [(remix, None, True), (live, original, True)]
    for proposal in proposals:
        versions.apply_version_proposal(session, proposal)
    assert (live.title, live.version_kind, live.version_label, live.original) == ("Boję się", "live", None, original)
    assert title_with_version(live) == "Boję się (Live)"
    assert title_with_version(remix) == "Iskrzy Iskrzy! (Agim Remix)"
    assert title_with_cover(live) == "Boję się (Live)"
    assert versions.find_versions(session) == []
    assert versions.versions_of(session, original) == [live]
    assert versions.versions_of(session, live) == [original]


def test_a_plain_version_marker_is_offered_unticked(session):
    _song(session, "Beyoncé", "Crazy in Love (URM version)")
    assert [p.certain for p in versions.find_versions(session)] == [False]


def test_a_version_points_at_the_original_never_at_another_version(session):
    original = _song(session, "A", "Song")
    live = _song(session, "A", "Song", version_kind="live")
    versions.set_version([live], "live", None, original)
    acoustic = _song(session, "A", "Song", version_kind="acoustic")
    versions.set_version([acoustic], "acoustic", "Live Acoustic", live)
    assert acoustic.version_of == original.id
    with pytest.raises(ValueError):
        versions.set_version([original], "live", None, original)
    with pytest.raises(ValueError):
        versions.set_version([original], "bonus")
    versions.set_version([acoustic], None, "ignored", original)
    assert (acoustic.version_kind, acoustic.version_label, acoustic.version_of) == (None, None, None)


def test_matching_keeps_versions_and_originals_apart(session):
    original = _song(session, "Nosowska", "Boję się")
    live = _song(session, "Nosowska", "Boję się", version_kind="live", version_of=original.id)
    legacy = _song(session, "Nosowska", "Zanim (Live)")
    songs = [original, live, legacy]
    assert songs_for_entry(songs, "Boję się") == [original, legacy]
    assert songs_for_entry(songs, "Boję się (Live)") == [live, legacy]
    assert songs_for_entry(songs, "Boję się (Acoustic)") == []
    assert find_existing_song("Boję się (Live)", songs) is live
    assert find_existing_song("Boję się", songs) is original
    assert find_song_loosely("Boję się (Live) (Official Video)", songs) is live
    assert find_existing_song("Zanim (Live)", songs) is legacy


def test_the_same_marker_is_tried_first(session):
    remix = _song(session, "A", "Song", version_kind="remix", version_label="X Remix")
    other = _song(session, "A", "Song", version_kind="remix", version_label="Y Remix")
    assert songs_for_entry([remix, other], "Song (Y Remix)") == [other, remix]
    assert find_existing_song("Song (Y Remix)", [remix, other]) is other


def test_a_marker_run_on_without_a_bracket_names_that_version(session):
    original = _song(session, "Brodka", "Dancing Shoes")
    remix = _song(session, "Brodka", "Dancing Shoes", version_kind="remix", version_label="Kamp! remix",
                  version_of=original.id)
    songs = [original, remix]
    assert songs_for_entry(songs, "Dancing Shoes KAMP! REMIX") == [original, remix]
    assert find_existing_song("Dancing Shoes KAMP! REMIX", songs) is remix
    assert find_existing_song("Dancing Shoes Remix", songs) is None
    assert songs_for_entry(songs, "Dancing Shoes") == [original]


def test_a_plain_version_marker_narrows_nothing(session):
    bach = _song(session, "Bach", "Toccata")
    urm = _song(session, "Bach", "Toccata", version_kind="other", version_label="URM version")
    live = _song(session, "Bach", "Toccata", version_kind="live")
    assert songs_for_entry([bach, urm, live], "Toccata (Best Version Ever)") == [bach]
    assert songs_for_entry([bach, urm, live], "Toccata (URM version)") == [urm, bach]
    song = create_song_entry({"title": "Fugue (Best Version Ever)"}, bach.artist, session)
    assert (song.title, song.version_kind) == ("Fugue (Best Version Ever)", None)


def test_a_genre_version_is_kept_apart_from_the_original(session):
    # A song request "Heart Of Glass (Disco Version)" was settled as the plain song
    original = _song(session, "Blondie", "Heart of Glass")
    assert songs_for_entry([original], "Heart Of Glass (Disco Version)") == []
    assert find_existing_song("Heart Of Glass (Disco Version)", [original]) is None
    song = create_song_entry({"title": "Heart Of Glass (Disco Version)"}, original.artist, session)
    assert (song.title, song.version_kind, song.version_label, song.version_of) == (
        "Heart Of Glass", "other", "Disco Version", original.id)
    assert songs_for_entry([original, song], "Heart of Glass (wersja disco)") == [song]


def test_a_new_song_gets_its_version_and_original(session):
    original = _song(session, "Krzysztof Zalewski", "Dziwny jest ten świat")
    live = create_song_entry({"title": "Dziwny jest ten świat (Live)"}, original.artist, session)
    assert (live.title, live.version_kind, live.version_of) == ("Dziwny jest ten świat", "live", original.id)
    remix = create_song_entry({"title": "Nowa (Remix)"}, original.artist, session)
    assert (remix.title, remix.version_kind, remix.version_of) == ("Nowa", "remix", None)


def test_duplicates_tell_a_version_from_its_original(session):
    original = _song(session, "A", "Song")
    _song(session, "A", "Song", version_kind="live", version_of=original.id)
    assert find_duplicate_groups(session) == []
    legacy = _song(session, "A", "Song (Live)")
    groups = find_duplicate_groups(session)
    assert len(groups) == 1 and legacy in groups[0] and original not in groups[0]


def test_a_merge_moves_the_versions_and_a_deletion_drops_the_original(session):
    original = _song(session, "A", "Song")
    twin = _song(session, "A", "song")
    live = _song(session, "A", "Song", version_kind="live", version_of=twin.id)
    merge_songs(session, original, twin)
    session.flush()
    assert live.version_of == original.id
    session.delete(original)
    session.flush()
    assert live.version_of is None


def test_merging_a_marked_title_into_its_version_keeps_one_marker(session):
    version = _song(session, "A", "Song", version_kind="live")
    legacy = _song(session, "A", "Song (Live)")
    merge_songs(session, legacy, version)
    assert (legacy.title, legacy.version_kind) == ("Song", "live")
    assert title_with_version(legacy) == "Song (Live)"
