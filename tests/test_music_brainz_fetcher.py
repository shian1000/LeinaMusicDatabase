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


def test_release_group_without_a_type_is_not_a_songs_album():
    # Real result for "Deep Blue Something - Breakfast at Tiffanys": the search
    # hit's only release is a radio station's compilation nobody typed.
    untyped = {"title": "Radio Woodstock 100.1 WDST 25th Anniversary - Disc 2",
               "release-group": {"title": "Radio Woodstock 100.1 WDST 25th Anniversary - Disc 2"}}
    assert _pick_release([_recording("Breakfast at Tiffanys", [untyped])]) == (None, None)


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


def test_get_album_name_sleeps_its_own_delay_once(monkeypatch):
    # DSC-03: the second (spell-checked) pass called itself without delay=,
    # so it always slept the default 1.0 s
    monkeypatch.setattr(music_brainz_fetcher.musicbrainzngs, "search_recordings",
                        lambda query, limit: {"recording-list": NOTION_RECORDINGS})
    monkeypatch.setattr(music_brainz_fetcher, "check_spelling", lambda artist, song: {"found": False})
    slept = []
    monkeypatch.setattr(music_brainz_fetcher.time, "sleep", slept.append)

    assert get_album_name("The Rare Occasions", "Notion", delay=0.25).album == "Futureproof"
    assert slept == [0.25]


def _counting_search(monkeypatch, results_for):
    """search_recordings() answering by the queried title; returns the list of
    (query, limit) calls and check_spelling() calls."""
    searches, spell_checks = [], []

    def search(query, limit):
        searches.append((query, limit))
        return {"recording-list": next((r for title, r in results_for.items() if f'recording:"{title}"' in query), [])}
    monkeypatch.setattr(music_brainz_fetcher.musicbrainzngs, "search_recordings", search)
    monkeypatch.setattr(music_brainz_fetcher.time, "sleep", lambda seconds: None)
    return searches, spell_checks


def test_get_album_name_takes_the_first_hit_without_a_spell_check(monkeypatch):
    # DSC-12: a hit was thrown away and searched again after check_spelling() -
    # at least three MusicBrainz requests for every album found
    searches, spell_checks = _counting_search(monkeypatch, {"Notion": NOTION_RECORDINGS})
    monkeypatch.setattr(music_brainz_fetcher, "check_spelling",
                        lambda artist, song: spell_checks.append(song) or {"found": False})

    assert get_album_name("The Rare Occasions", "Notion").album == "Futureproof"
    assert [limit for _, limit in searches] == [5]
    assert spell_checks == []


def test_get_album_name_tries_the_corrected_spelling_only_when_nothing_was_found(monkeypatch):
    # DSC-12: a miss never got a spell check at all
    searches, spell_checks = _counting_search(monkeypatch, {"Notion": NOTION_RECORDINGS})
    monkeypatch.setattr(music_brainz_fetcher, "check_spelling", lambda artist, song: spell_checks.append(song) or {
        "found": True, "corrected_artist": "The Rare Occasions", "corrected_title": "Notion"})

    result = get_album_name("The Rare Ocasions", "Notoin")

    assert result.album == "Futureproof"
    assert spell_checks == ["Notoin"]
    assert [limit for _, limit in searches] == [5, 25, 5]
    assert 'recording:"Notion"' in searches[-1][0]


def test_get_album_name_does_not_search_again_when_the_spelling_is_already_right(monkeypatch):
    searches, spell_checks = _counting_search(monkeypatch, {})
    monkeypatch.setattr(music_brainz_fetcher, "check_spelling", lambda artist, song: spell_checks.append(song) or {
        "found": True, "corrected_artist": artist, "corrected_title": song})

    assert get_album_name("The Rare Occasions", "Unreleased Song") is None
    assert [limit for _, limit in searches] == [5, 25]
    assert spell_checks == ["Unreleased Song"]


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
    # "remix" is in the user's blacklist, so the album "Death By Rock and Roll Remix" drops out;
    # the singles "25" and "And So It Went" carry the song only as an extra track
    recordings = [_recording("Death by Rock and Roll", [
        _release("Death By Rock and Roll Remix", "Album", "2021-02-12"),
        _release("25", "Single", "2020-11-13"),
        _release("And So It Went", "Single", "2021-01-08"),
    ])]
    assert _pick_release(recordings) == (None, None)


def test_a_common_word_doesnt_drop_the_real_album():
    # "rock" in a title no longer blacklists it (COM-09): it's The Pretty Reckless' album
    recordings = [_recording("Death by Rock and Roll", [
        _release("Death By Rock and Roll", "Album", "2021-02-12"),
        _release("25", "Single", "2020-11-13"),
    ])]
    assert _pick_release(recordings)[0] == "Death By Rock and Roll"


def test_a_single_named_after_the_song_still_counts():
    recordings = [_recording("25", [_release("25", "Single", "2020-11-13")])]
    assert _pick_release(recordings)[0] == "25"


def test_blacklisted_album_the_artist_already_has_is_picked(monkeypatch):
    # "Death By Rock and Roll Remix" trips the user's "remix" blacklist entry; once the database
    # has it as The Pretty Reckless' album, it's that artist's real album again
    from utils.database import albums
    monkeypatch.setattr(albums, "artist_has_album",
                        lambda artist_name, title: artist_name == "The Pretty Reckless"
                        and title == "Death By Rock and Roll Remix")
    recordings = [_recording("Death by Rock and Roll", [
        _release("Death By Rock and Roll Remix", "Album", "2021-02-12"),
        _release("25", "Single", "2020-11-13"),
    ])]
    assert _pick_release(recordings, "The Pretty Reckless")[0] == "Death By Rock and Roll Remix"
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

    # Real result for "Daan - Housewife", trimmed: a film soundtrack listed
    # before the artist's own album on the same recording
    daan = ("daan-id", "Daan")
    housewife = {"title": "Housewife", "artist-credit": _credit(daan),
                 "release-list": [_by(_release("Windkracht 10 Soundtrack", "Album", None, ["Soundtrack"]),
                                      (VARIOUS_ARTISTS, "Various Artists")),
                                  _release("Housewife", "Single", "2004-05-19"),
                                  _release("Victory", "Album", "2004-11-29")]}
    assert _pick_release([housewife])[0] == "Victory"

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
    monkeypatch.setattr(music_brainz_fetcher, "check_spelling", lambda artist, song: {"found": False})
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


def _placed_release(title, primary_type, number, disc=1, medium_count=1, release_id="rel-1"):
    """A search-result release as MusicBrainz sends it: the medium-list holds
    only the searched track, with its number on that medium."""
    release = _release(title, primary_type, "2021-07-15")
    release.update({"id": release_id, "medium-count": medium_count,
                    "medium-list": [{"position": str(disc), "track-list": [{"number": number}]}]})
    return release


def _mock_search(monkeypatch, recordings):
    monkeypatch.setattr(music_brainz_fetcher.musicbrainzngs, "search_recordings",
                        lambda query, limit: {"recording-list": recordings})
    monkeypatch.setattr(music_brainz_fetcher, "check_spelling", lambda artist, song: {"found": False})
    monkeypatch.setattr(music_brainz_fetcher.time, "sleep", lambda seconds: None)


def test_get_album_name_reports_the_track_number_of_the_picked_release(monkeypatch):
    # Shaped like the real result for "Serial Experiments - Dust of Harmonies": track 7 of 26
    _mock_search(monkeypatch, [_recording("Dust Of Harmonies (REMASTERED)", [
        _placed_release("Lain: The Wired World (Remastered)", "Album", "7")])])

    result = get_album_name("The Rare Occasions", "Dust of Harmonies")

    assert result.album == "Lain: The Wired World (Remastered)"
    assert (result.track_number, result.disc_number) == (7, None)  # one disc: NULL = 1


def test_disc_number_only_on_a_release_of_several_discs_and_a_vinyl_side_is_no_number(monkeypatch):
    _mock_search(monkeypatch, [_recording("Notion", [_placed_release("Futureproof", "Album", "3", disc=2,
                                                                     medium_count=2)])])
    result = get_album_name("The Rare Occasions", "Notion")
    assert (result.track_number, result.disc_number) == (3, 2)

    _mock_search(monkeypatch, [_recording("Notion", [_placed_release("Futureproof", "Album", "A1")])])
    assert get_album_name("The Rare Occasions", "Notion").track_number is None


def test_tracklist_loads_only_when_asked_and_only_for_an_album_or_ep(monkeypatch):
    lookups = []

    def get_release_by_id(release_id, includes):
        lookups.append((release_id, includes))
        return {"release": {"artist-credit-phrase": "The Rare Occasions", "medium-list": [
            {"position": "1", "track-list": [
                {"number": "1", "position": "1", "recording": {"title": "Notion"}},
                {"number": "A2", "position": "2", "title": "Alright (Edit)",
                 "recording": {"title": "Alright", "artist-credit-phrase": "The Rare Occasions & Guest"}},
            ]},
            {"position": "2", "track-list": [{"number": "1", "position": "1", "recording": {"title": "Kinda"}}]},
        ]}}
    monkeypatch.setattr(music_brainz_fetcher.musicbrainzngs, "get_release_by_id", get_release_by_id)
    _mock_search(monkeypatch, [_recording("Notion", [_placed_release("Futureproof", "EP", "1",
                                                                     release_id="ep-id")])])

    result = get_album_name("The Rare Occasions", "Notion")
    assert lookups == []  # the search alone costs no tracklist request

    tracks = result.load_tracks()
    assert lookups == [("ep-id", ["recordings", "artist-credits"])]
    assert [(t.title, t.track_number, t.disc_number, t.artist) for t in tracks] == [
        ("Notion", 1, 1, "The Rare Occasions"),
        ("Alright (Edit)", 2, 1, "The Rare Occasions & Guest"),  # the track's own title, "A2" -> position
        ("Kinda", 1, 2, "The Rare Occasions"),
    ]

    # A single's other tracks aren't the album we want
    _mock_search(monkeypatch, [_recording("Notion", [_placed_release("Notion", "Single", "1")])])
    assert get_album_name("The Rare Occasions", "Notion").load_tracks is None


def test_a_failed_tracklist_request_gives_no_tracks(monkeypatch):
    def fail(release_id, includes):
        raise music_brainz_fetcher.musicbrainzngs.NetworkError("down")
    monkeypatch.setattr(music_brainz_fetcher.musicbrainzngs, "get_release_by_id", fail)
    _mock_search(monkeypatch, [_recording("Notion", [_placed_release("Futureproof", "Album", "1")])])

    assert get_album_name("The Rare Occasions", "Notion").load_tracks() == []


def test_a_singles_year_isnt_taken_from_someone_elses_release(monkeypatch):
    # DSC-46: the single's year had only the type filter; get_album_name() also refuses an edition
    # credited to another artist (Dido's "No Angel" with LeAnn Rimes songs appended)
    leann = [{"artist": {"id": "leann", "name": "LeAnn Rimes"}}]
    dido = [{"artist": {"id": "dido", "name": "Dido"}}]
    other = {**_release("No Angel", "Album", "1999-06-01"), "artist-credit": dido}
    own = {**_release("Can't Fight the Moonlight", "Single", "2000-10-24"), "artist-credit": leann}
    recording = {"title": "Can't Fight the Moonlight", "artist-credit": leann, "release-list": [other, own]}
    _mock_search(monkeypatch, [recording])

    result = music_brainz_fetcher.get_release_year("LeAnn Rimes", "Can't Fight the Moonlight", is_single=True)
    assert result.year == 2000


def _grouped_release(title, primary_type, date, group_id, secondary_types=()):
    release = _placed_release(title, primary_type, "4")
    release["date"] = date
    release["release-group"].update({"id": group_id, "title": title, "secondary-type-list": list(secondary_types)})
    return release


def test_album_comes_with_the_earliest_year_of_its_release_group_its_type_and_id(monkeypatch):
    # The picked edition is a remaster; another hit holds the group's first edition
    _mock_search(monkeypatch, [
        _recording("Notion", [_grouped_release("Futureproof", "Album", "2021-07-15", "rg-1")]),
        # (a later album of the same recording, which the earliest-album rule leaves out)
        _recording("Notion", [_grouped_release("Futureproof", "Album", "2016-08-05", "rg-1"),
                              _grouped_release("Other", "Album", "2017", "rg-2")]),
    ])

    result = get_album_name("The Rare Occasions", "Notion")

    assert (result.album, result.year, result.album_type) == ("Futureproof", 2016, "album")
    assert result.album_ids == {"musicbrainz_release_group_id": "rg-1"}


def test_a_soundtrack_is_typed_so_and_a_single_gets_no_type(monkeypatch):
    _mock_search(monkeypatch, [_recording("Notion", [
        _grouped_release("Some Film", "Album", "2016", "rg-1", ["Soundtrack"])])])
    assert get_album_name("The Rare Occasions", "Notion").album_type == "soundtrack"

    _mock_search(monkeypatch, [_recording("Notion", [_grouped_release("Notion", "Single", "2016", "rg-3")])])
    result = get_album_name("The Rare Occasions", "Notion")
    assert (result.album, result.album_type, result.year) == ("Notion", None, 2016)


# Real search results for ex-Yugoslav songs (2026-11-19), trimmed. Each got a
# later release's album and year because the first Album listed won.
def _yu_release(title, date, group_id, primary_type="Album", secondary_types=(), artist=None):
    release = _grouped_release(title, primary_type, date, group_id, secondary_types)
    if artist:
        release["artist-credit"] = [{"artist": {"id": artist, "name": artist}}]
    return release


def _yu_recording(title, releases):
    return {"title": title, "artist-credit": [{"artist": {"id": "own", "name": "Tajči"}}], "release-list": releases}


def test_the_earliest_album_wins_not_the_first_listed():
    # Tajči - "Hajde da ludujemo": "Bube u glavi" (1991) came first, the 1990 album later
    recordings = [
        _yu_recording("Hajde Da Ludujemo", [_yu_release("The Best Of", None, "rg-best", None, ["Compilation"])]),
        _yu_recording("Hajde da ludujemo", [
            _yu_release("90-e - 120 originalnih hitova", "2012", "rg-90", secondary_types=["Compilation"]),
            _yu_release("Bube u glavi", "1991", "rg-bube", artist="own"),
        ]),
        _yu_recording("Hajde Da Ludujemo", [_yu_release("Hajde Da Ludujemo", "1990", "rg-hajde", artist="own")]),
    ]
    assert _pick_release(recordings, "Tajči", "Hajde da ludujemo")[0] == "Hajde Da Ludujemo"


def test_a_recording_titled_otherwise_goes_last():
    # Its album would be dropped by the manager's title check anyway
    recordings = [
        _yu_recording("Lipe cvatu, sve je isto k'o i lani", [_yu_release("Bijelo dugme", "1984", "rg-1")]),
        _yu_recording("Lipe cvatu", [_yu_release("Some Album", "1985", "rg-2")]),
    ]
    assert _pick_release(recordings, title="Lipe cvatu")[0] == "Some Album"
    assert _pick_release(recordings)[0] == "Bijelo dugme"


def test_a_release_years_after_the_song_first_appeared_is_a_reissue():
    # Denis & Denis - "Soba 23" (1984): "Program tvog kompjutera" (1995) is a
    # best-of typed a plain Album; the recording was on a 1988 EP already
    recordings = [
        _yu_recording("Soba 23", [_yu_release("Riječki Novi Val: Antologija", "2008", "rg-a",
                                              secondary_types=["Compilation"])]),
        _yu_recording("Soba 23", [
            _yu_release("The Best of Denis & Denis", "2006", "rg-b", secondary_types=["Compilation"]),
            _yu_release("Ja sam lažljiva", "1988-01-28", "rg-ep", "EP"),
            _yu_release("Program tvog kompjutera", "1995", "rg-p"),
        ]),
    ]
    assert _pick_release(recordings, title="Soba 23")[0] == "Ja sam lažljiva"


def test_a_reissue_is_taken_last_and_without_its_year(monkeypatch):
    # Czesław Niemen - "Wspomnienie": "Dziwny jest ten świat" (1967) is among the
    # hits only as its 1991 edition, after a 1979 best-of
    _mock_search(monkeypatch, [
        _yu_recording("Wspomnienie", [_yu_release("The Best Of Niemen", "1979", "rg-b",
                                                  secondary_types=["Compilation"])]),
        _yu_recording("Wspomnienie", [_yu_release("Dziwny jest ten świat", "1991", "rg-d")]),
    ])
    result = get_album_name("Czesław Niemen", "Wspomnienie")
    assert (result.album, result.year) == ("Dziwny jest ten świat", None)


def test_a_lead_single_a_year_or_two_earlier_doesnt_make_the_album_a_reissue():
    recordings = [_yu_recording("Song", [_yu_release("Song", "2010", "rg-s", "Single"),
                                         _yu_release("The Album", "2012", "rg-a")])]
    assert _pick_release(recordings, title="Song")[0] == "The Album"


def test_a_singles_year_is_the_earliest_release_not_the_first_listed(monkeypatch):
    # Josipa Lisac - "O jednoj mladosti" (1973) got 2018, a studio session listed first
    _mock_search(monkeypatch, [
        _yu_recording("O jednoj mladosti", [_yu_release("Live", "2002", "rg-l", "Other", ["Live"])]),
        _yu_recording("O Jednoj Mladosti", [_yu_release("...tu u mojoj duši stanuješ...", "2018-12", "rg-t")]),
        _yu_recording("O jednoj mladosti", [_yu_release("Dnevnik jedne ljubavi", "1973", "rg-d"),
                                            _yu_release("Dnevnik jedne ljubavi", "1993", "rg-d")]),
    ])
    result = music_brainz_fetcher.get_release_year("Josipa Lisac", "O jednoj mladosti", is_single=True)
    assert (result.year, result.matched_title) == (1973, "O jednoj mladosti")
