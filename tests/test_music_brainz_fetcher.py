import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from utils.discoveries.discovery_modules import music_brainz_fetcher
from utils.discoveries.discovery_modules.music_brainz_fetcher import _pick_release, get_album_name


def _release(title, primary_type, date=None, secondary_types=()):
    release = {"title": title, "release-group": {"primary-type": primary_type,
                                                 "secondary-type-list": list(secondary_types)}}
    if date:
        release["date"] = date
    return release


def _recording(title, releases):
    return {"title": title, "artist-credit": [{"artist": {"name": "The Rare Occasions"}}],
            "release-list": releases}


# Real MusicBrainz search result for "The Rare Occasions - Notion", trimmed:
# the compilations and the later singles come before the EP the song came out on.
NOTION_RECORDINGS = [
    _recording("Notion", [
        _release("Viral Rock Hits", "Album", "2023-03-24", ["Compilation"]),
        _release("Indie/Rock Playlist: December 2016", "Other", "2016-12-16", ["Compilation"]),
        _release("Notion (Cinematic)", "Single"),
        _release("Notion", "Single", "2021-12-02"),
        _release("Rock Summer", "Album", "2022-06-10", ["Compilation"]),
        _release("Futureproof", "EP", "2016-08-05"),
    ]),
    _recording("Notion (Acoustic)", [_release("Notion", "Single", "2021-12-02")]),
]


def test_ep_beats_earlier_listed_singles_when_there_is_no_album():
    title, recording = _pick_release(NOTION_RECORDINGS)
    assert title == "Futureproof"
    assert recording["title"] == "Notion"


def test_studio_album_still_wins_over_ep():
    recordings = [_recording("Song", [
        _release("Early Days", "EP", "2010-01-01"),
        _release("Into the Night", "Album", "2012-01-01"),
    ])]
    assert _pick_release(recordings)[0] == "Into the Night"


def test_earliest_single_wins_and_undated_goes_last():
    recordings = [_recording("Song", [
        _release("Song (Remastered)", "Single"),
        _release("Song (Live Version)", "Single", "2019"),
        _release("Song", "Single", "2015-05"),
    ])]
    assert _pick_release(recordings)[0] == "Song"


def test_later_recording_cannot_outrank_the_first_ones_releases():
    # An acoustic version's earlier EP shouldn't replace the main recording's single.
    recordings = [
        _recording("Song", [_release("Song", "Single", "2020-01-01")]),
        _recording("Song (Acoustic)", [_release("Acoustic Sessions", "EP", "2018-01-01")]),
    ]
    assert _pick_release(recordings)[0] == "Song"


def test_only_compilations_gives_nothing():
    recordings = [_recording("Song", [_release("Hits 2020", "Album", "2020", ["Compilation"])])]
    assert _pick_release(recordings) == (None, None)


def test_get_album_name_returns_the_ep(monkeypatch):
    monkeypatch.setattr(music_brainz_fetcher.musicbrainzngs, "search_recordings",
                        lambda query, limit: {"recording-list": NOTION_RECORDINGS})
    monkeypatch.setattr(music_brainz_fetcher, "check_spelling", lambda artist, song: {"found": False})
    monkeypatch.setattr(music_brainz_fetcher.time, "sleep", lambda seconds: None)

    result = get_album_name("The Rare Occasions", "Notion")

    assert result.album == "Futureproof"
    assert result.matched_title == "Notion"


def test_get_album_name_keeps_its_delay_in_the_spell_checked_pass(monkeypatch):
    # DSC-03: the second (spell-checked) pass called itself without delay=,
    # so it always slept the default 1.0 s
    monkeypatch.setattr(music_brainz_fetcher.musicbrainzngs, "search_recordings",
                        lambda query, limit: {"recording-list": NOTION_RECORDINGS})
    monkeypatch.setattr(music_brainz_fetcher, "check_spelling", lambda artist, song: {"found": False})
    slept = []
    monkeypatch.setattr(music_brainz_fetcher.time, "sleep", slept.append)

    assert get_album_name("The Rare Occasions", "Notion", delay=0.25).album == "Futureproof"
    assert slept == [0.25]


def test_get_album_name_reports_the_queried_artist_among_several_credits(monkeypatch):
    # "El tango de Roxanne" is credited "Ewan McGregor + José Feliciano + Jacek Koman"; reporting
    # the first credit made the manager discard the result for José Feliciano.
    recording = {"title": "El tango de Roxanne",
                 "artist-credit": [{"artist": {"name": "Ewan McGregor"}}, " & ",
                                   {"artist": {"name": "José Feliciano"}}, " & ",
                                   {"artist": {"name": "Jacek Koman"}}],
                 "release-list": [_release("Moulin Rouge", "Album", "2001-05-08", ["Soundtrack"])]}
    monkeypatch.setattr(music_brainz_fetcher.musicbrainzngs, "search_recordings",
                        lambda query, limit: {"recording-list": [recording]})
    monkeypatch.setattr(music_brainz_fetcher, "check_spelling", lambda artist, song: {"found": False})
    monkeypatch.setattr(music_brainz_fetcher.time, "sleep", lambda seconds: None)

    result = get_album_name("José Feliciano", "El tango de Roxanne")

    assert result.album == "Moulin Rouge"
    assert result.matched_artist == "José Feliciano"


def test_album_title_comes_from_the_release_group_not_one_edition():
    # One edition of Low Roar's "0" is entered as "O" (letter) and comes first in the results
    edition = _release("O", "Album", "2014-08-05")
    edition["release-group"]["title"] = "0"
    recordings = [_recording("I'll Keep Coming", [edition])]
    assert _pick_release(recordings)[0] == "0"


def test_another_songs_single_is_not_this_songs_album():
    # "rock" is in the user's blacklist, so the album "Death By Rock and Roll" and the singles of
    # that name drop out; the singles "25" and "And So It Went" carry the song only as an extra track
    recordings = [_recording("Death by Rock and Roll", [
        _release("Death By Rock and Roll", "Album", "2021-02-12"),
        _release("Death by Rock and Roll", "Single", "2020-05-15"),
        _release("25", "Single", "2020-11-13"),
        _release("And So It Went", "Single", "2021-01-08"),
    ])]
    assert _pick_release(recordings) == (None, None)


def test_a_single_named_after_the_song_still_counts():
    recordings = [_recording("25", [_release("25", "Single", "2020-11-13")])]
    assert _pick_release(recordings)[0] == "25"


def test_blacklisted_album_the_artist_already_has_is_picked(monkeypatch):
    # "Death By Rock and Roll" trips the user's "rock" blacklist entry; once the database has it
    # as The Pretty Reckless' album, it's that artist's real album again
    from utils.database import albums
    monkeypatch.setattr(albums, "artist_has_album",
                        lambda artist_name, title: artist_name == "The Pretty Reckless"
                        and title == "Death By Rock and Roll")
    recordings = [_recording("Death by Rock and Roll", [
        _release("Death By Rock and Roll", "Album", "2021-02-12"),
        _release("25", "Single", "2020-11-13"),
    ])]
    assert _pick_release(recordings, "The Pretty Reckless")[0] == "Death By Rock and Roll"
    assert _pick_release(recordings)[0] is None


LEANN_RIMES, DIDO = "a5a8bd8a-leann-rimes", "f3c06d7c-dido"
from config.constants import MUSICBRAINZ_VARIOUS_ARTISTS_ID as VARIOUS_ARTISTS


def _credit(*artists):
    return [{"artist": {"id": artist_id, "name": name}} for artist_id, name in artists]


def _by(release, *artists):
    release["artist-credit"] = _credit(*artists)
    return release


def test_another_artists_album_is_not_the_songs_album():
    # An Australian edition of Dido's "No Angel" has four LeAnn Rimes songs appended; it's Dido's album
    recording = {"title": "Can’t Fight the Moonlight", "artist-credit": _credit((LEANN_RIMES, "LeAnn Rimes")),
                 "release-list": [
                     _by(_release("No Angel", "Album"), (DIDO, "Dido")),
                     _by(_release("Twisted Angel", "Album", "2002"), (LEANN_RIMES, "LeAnn Rimes")),
                 ]}
    assert _pick_release([recording])[0] == "Twisted Angel"


def test_various_artists_album_and_a_co_artists_album_still_count():
    feliciano, mcgregor = ("feliciano-id", "José Feliciano"), ("mcgregor-id", "Ewan McGregor")
    soundtrack = {"title": "El tango de Roxanne", "artist-credit": _credit(mcgregor, feliciano),
                  "release-list": [_by(_release("Moulin Rouge", "Album", "2001", ["Soundtrack"]),
                                       (VARIOUS_ARTISTS, "Various Artists"))]}
    assert _pick_release([soundtrack])[0] == "Moulin Rouge"

    # The release artist is one of the recording's artists, not the queried one
    duet = {"title": "Duet", "artist-credit": _credit(mcgregor, feliciano),
            "release-list": [_by(_release("McGregor Sings", "Album", "2003"), mcgregor)]}
    assert _pick_release([duet])[0] == "McGregor Sings"


def test_quotes_and_backslashes_in_names_are_escaped_in_the_query(monkeypatch):
    # DSC-07: titles were put between quotes unescaped - 'Blue Monday 12" Mix'
    # ended the phrase early and broke the Lucene query
    queries = []

    def search(query, limit):
        queries.append(query)
        return {"recording-list": []}
    monkeypatch.setattr(music_brainz_fetcher.musicbrainzngs, "search_recordings", search)
    monkeypatch.setattr(music_brainz_fetcher.musicbrainzngs, "search_release_groups",
                        lambda query, limit: queries.append(query) or {"release-group-list": []})
    monkeypatch.setattr(music_brainz_fetcher.time, "sleep", lambda seconds: None)

    get_album_name('New "Order"', 'Blue Monday 12" Mix')
    music_brainz_fetcher.get_release_year("AC\\DC", 'Live "Wire"', is_single=False)

    assert queries[0] == 'recording:"Blue Monday 12\\" Mix" AND artist:"New \\"Order\\""'
    assert queries[-2:] == ['releasegroup:"Live \\"Wire\\"" AND artist:"AC\\\\DC"',
                           'release:"Live \\"Wire\\"" AND artist:"AC\\\\DC"']  # the various-artists fallback


def test_lucene_quote_is_shared_with_the_genre_lookup():
    from utils.common.musicbrainz_client import lucene_quote
    from utils.discoveries import genre_lookup

    assert lucene_quote('a "b" \\c') == '"a \\"b\\" \\\\c"'
    assert genre_lookup.lucene_quote is lucene_quote
