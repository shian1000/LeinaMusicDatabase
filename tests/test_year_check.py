"""Fill missing data -> "Years" (fill_missing_years.py), what the merged
"Check stored years" brought: step 2 holds the stored years against
MusicBrainz's first release - one request per album group (a single alone),
a year more than MUSICBRAINZ_REISSUE_AFTER_YEARS later offered (none ticked),
a ticked one written to every song of its album, the songs looked at marked
year_checked so they aren't asked again; a song MusicBrainz knows later or
not at all left alone, one it couldn't be asked about left unchecked,
versions / projects / full albums not asked; whole groups within what step 1
left of max-songs-per-fetch. Step 1 now also looks up songs with no album (as
singles) and a project's recordings (the artist's year, else the project as
the album, not held against the studio song, always reviewed). MusicBrainz,
the modules and tag.db faked, in-memory music.db."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import questionary
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import menu.main_menu  # noqa: F401,E402  (import order, see AGENTS.md)
from config.constants import SINGLES_ALBUM, SONG_KIND_FULL_ALBUM  # noqa: E402
from utils.database.datatables import Artist, Base, Project, Song  # noqa: E402
from utils.discoveries.discovery_result import YearDiscoveryResult  # noqa: E402

# The package's fill_missing_years() function shadows the module of that name
years = importlib.import_module(
    "menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data.fill_missing_years")


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _songs(session):
    tresb, maanam = Artist(name="très.b"), Artist(name="Maanam")
    songs = {
        "reissue": Song(title="Ola", artist=tresb, year=2013, album="Męskie Granie"),
        "same_album": Song(title="Pa", artist=tresb, year=2013, album="Męskie Granie"),
        "close": Song(title="Close", artist=tresb, year=2009, album=SINGLES_ALBUM),  # within the margin
        "earlier": Song(title="Cykady na Cykladach", artist=maanam, year=1981),  # MusicBrainz lacks the single
        "unknown": Song(title="Unknown", artist=maanam, year=2000),
        "failing": Song(title="Failing", artist=maanam, year=2000),
        "version": Song(title="Ola", artist=tresb, year=2015, version_kind="live"),
        "full": Song(title="Album", artist=tresb, year=2015, kind=SONG_KIND_FULL_ALBUM),
    }
    session.add_all(songs.values())
    session.flush()
    return songs


def _run(session, monkeypatch, room=50, tick=lambda choices: [c.value for c in choices], checked=()):
    firsts = {"Ola": 2007, "Close": 2007, "Cykady na Cykladach": 1991, "Unknown": None,
              "Club Foot": 2004, "Fire": 2009, "Runaway": 2004}
    asked, tagged, shown = [], [], []

    def first_appearance_year(artist, title, raise_errors=False):
        asked.append(title)
        if title == "Failing":
            raise ConnectionError("no network")
        return firsts[title]

    monkeypatch.setattr(years, "first_appearance_year", first_appearance_year)
    monkeypatch.setattr(years, "require_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(years, "song_ids_with_tag", lambda tag: set(checked))
    monkeypatch.setattr(years, "add_tag_to_songs", lambda ids, tag: tagged.extend(ids))

    def checkbox(_question, choices):
        shown.extend(choice.title for choice in choices)
        assert not any(choice.checked for choice in choices)
        picked = tick(choices)
        return type("Prompt", (), {"ask": lambda self: picked})()
    monkeypatch.setattr(questionary, "checkbox", checkbox)
    changed = years._check_stored_years(room, set())
    return changed, asked, set(tagged), shown


def test_a_reissues_year_is_offered_and_changed_on_its_whole_album(session, monkeypatch):
    songs = _songs(session)
    changed, asked, tagged, shown = _run(session, monkeypatch)

    assert {song.title for song in changed} == {"Ola", "Pa"}
    assert (songs["reissue"].year, songs["same_album"].year) == (2007, 2007)  # one album, one year
    assert shown == ['très.b - Męskie Granie (2 song(s), asked by "Ola"):  2013  ->  2007  '
                     "(MusicBrainz's first release)"]
    assert (songs["close"].year, songs["earlier"].year, songs["unknown"].year) == (2009, 1981, 2000)
    # one request for the album, none for the version and the full album
    assert sorted(asked) == ["Close", "Cykady na Cykladach", "Failing", "Ola", "Unknown"]
    ids = {name: song.id for name, song in songs.items()}
    assert tagged == {ids[name] for name in ("reissue", "same_album", "close", "earlier", "unknown")}


def test_an_album_with_several_stored_years_is_checked_song_by_song(session, monkeypatch):
    # the album text is wrong for some of them: asked by "Club Foot" alone, all three got 2004
    kasabian = Artist(name="Kasabian")
    album = "West Ryder Pauper Lunatic Asylum"
    club_foot, fire, runaway = (Song(title=title, artist=kasabian, year=year, album=album)
                                for title, year in (("Club Foot", 2004), ("Fire", 2009), ("Runaway", 2009)))
    session.add_all([club_foot, fire, runaway])
    session.flush()
    changed, asked, tagged, shown = _run(session, monkeypatch)

    assert sorted(asked) == ["Club Foot", "Fire", "Runaway"]
    assert changed == [runaway] and (club_foot.year, fire.year, runaway.year) == (2004, 2009, 2004)
    assert shown == [f"Kasabian - Runaway [{album}]:  2009  ->  2004  (MusicBrainz's first release)"]
    assert tagged == {club_foot.id, fire.id, runaway.id}


def test_cancelling_the_list_changes_nothing_and_asks_again(session, monkeypatch):
    songs = _songs(session)
    changed, _, tagged, _ = _run(session, monkeypatch, tick=lambda choices: None)

    assert changed == [] and songs["reissue"].year == 2013
    assert songs["reissue"].id not in tagged and songs["close"].id in tagged


def test_checked_songs_arent_asked_again(session, monkeypatch):
    songs = _songs(session)
    _, asked, _, _ = _run(session, monkeypatch, checked=[songs["reissue"].id, songs["same_album"].id,
                                                         songs["close"].id])

    assert sorted(asked) == ["Cykady na Cykladach", "Failing", "Unknown"]


def test_whole_groups_fit_in_what_step_one_left(session, monkeypatch):
    _songs(session)
    # by artist, album: Maanam's three singles, then très.b's - the album (2 songs) no longer fits in 4
    _, asked, _, _ = _run(session, monkeypatch, room=4)
    assert sorted(asked) == ["Cykady na Cykladach", "Failing", "Unknown"]
    _, asked, _, _ = _run(session, monkeypatch, room=1)
    assert len(asked) == 1  # always at least one group
    assert years._check_stored_years(0, set()) == []


def test_unattended_only_the_owners_rule_changes_a_stored_year(session, monkeypatch):
    from utils.ui import prompts
    songs = _songs(session)
    monkeypatch.setattr(years, "remember_misses", lambda kind, ids: None)
    monkeypatch.setattr(years, "recent_misses", lambda kind: set())
    with prompts.unattended():
        changed, _, tagged, _ = _run(session, monkeypatch)
    assert changed == [] and songs["reissue"].year == 2013 and songs["reissue"].id not in tagged

    monkeypatch.setattr(prompts, "user_rule_taking",
                        lambda rule, facts: "mine" if rule == "stored_years" and facts["years_later"] >= 6 else None)
    with prompts.unattended():
        changed, _, tagged, _ = _run(session, monkeypatch)
    assert songs["reissue"].year == 2007 and songs["reissue"].id in tagged


def test_first_appearance_year_raises_on_a_failed_request_only_when_asked_to(monkeypatch):
    import musicbrainzngs
    from utils.discoveries.discovery_modules import music_brainz_fetcher as mb

    def search_recordings(**kw):
        raise musicbrainzngs.NetworkError("down")
    monkeypatch.setattr(mb.musicbrainzngs, "search_recordings", search_recordings)

    assert mb.first_appearance_year("Maanam", "Cykady na Cykladach") is None
    with pytest.raises(musicbrainzngs.WebServiceError):
        mb.first_appearance_year("Maanam", "Cykady na Cykladach", raise_errors=True)


# ==================== step 1: songs with no album, projects' recordings ====================

def test_songs_without_an_album_and_projects_recordings_get_years(session, monkeypatch):
    mg, unplugged = Project(name="Męskie Granie"), Project(name="MTV Unplugged")
    orkiestra, nirvana, lao = Artist(name="Męskie Granie Orkiestra 2019"), Artist(name="Nirvana"), Artist(name="Lao Che")
    edition = Song(title="Wojenka", artist=orkiestra, project=mg)
    unplugged_song = Song(title="Where Did You Sleep Last Night?", artist=nirvana, project=unplugged)
    lao_song = Song(title="Automaty", artist=lao, project=mg)
    plain = Song(title="Lithium", artist=nirvana)
    session.add_all([edition, unplugged_song, lao_song, plain])
    session.flush()
    queries, edits = [], []

    def discover(artist, query, single, modules, synonyms=None, **kw):
        queries.append((artist, query, single, kw.get("held", True)))
        return {"MTV Unplugged": YearDiscoveryResult(year=1994), "Lithium": YearDiscoveryResult(year=1991)}.get(query)

    monkeypatch.setattr(years, "get_songs_missing_year", lambda: [edition, unplugged_song, lao_song, plain])
    monkeypatch.setattr(years, "load_discovery_modules", lambda kind: ["module"])
    monkeypatch.setattr(years, "discover_release_year_result", discover)
    monkeypatch.setattr(years, "release_year_from_stored_link", lambda songs: None)
    monkeypatch.setattr(years, "close_global_driver", lambda: None)
    monkeypatch.setattr(years, "get_known_album_year", lambda *a: None)
    monkeypatch.setattr(years, "ask_yes_no", lambda q, **k: False)
    monkeypatch.setattr(years, "load_max_songs_per_fetch", lambda: 100)
    shown = []
    monkeypatch.setattr(questionary, "checkbox",
                        lambda message, choices: shown.extend(c.title for c in choices) or SimpleNamespace(ask=lambda: [0]))

    touched, looked_up = years._fill_empty_years(None, None)

    assert edition.year == 2019  # the artist names the edition: no lookup
    assert sorted(queries) == [("Lao Che", "Męskie Granie", False, False), ("Nirvana", "Lithium", True, True),
                               ("Nirvana", "MTV Unplugged", False, False)]  # never the project song's title
    assert plain.year == 1991 and unplugged_song.year == 1994 and lao_song.year is None
    assert shown == ["Nirvana - MTV Unplugged (Where Did You Sleep Last Night?): 1994  (a release of MTV Unplugged)"]
    assert looked_up == 3 and set(touched) == {edition, plain, unplugged_song}
