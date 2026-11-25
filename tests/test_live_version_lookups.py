"""Fill missing data -> Albums / Years for a live version (songs.version_kind
"live"): the lookup searches by artist and title and finds the studio
original's release, so a value is saved unasked only when the release found is
live too - its title reads so, or the source says (MusicBrainz's "Live"
release group) - else the owner reviews it, and unattended nothing of it is
saved (owner, 2026-11-24). Real case 2026-11-22: "The Rolling Stones - The
Last Time" (live, video kvIIM2AZgCA) got the 1965 studio single's album and
year. Every lookup, prompt and DB write mocked."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import questionary

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

import menu.main_menu  # noqa: F401,E402  (import order, see AGENTS.md)
from config.constants import VERSION_ACOUSTIC, VERSION_LIVE, VERSION_REMIX  # noqa: E402
from utils.common.version_names import lookup_fits_version, reads_as_version  # noqa: E402
from utils.discoveries.discoveries_manager import AlbumMatch  # noqa: E402
from utils.discoveries.discovery_result import YearDiscoveryResult  # noqa: E402
from utils.discoveries.discovery_modules import music_brainz_fetcher  # noqa: E402
from utils.ui import prompts  # noqa: E402

_PKG = "menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data"

STUDIO_SINGLE = "The Last Time"
LIVE_RELEASE = "The Last Time (Live at the Beacon Theatre)"


def _song(title, kind=None, album=None):
    return SimpleNamespace(id=title, title=title, album=album, year=None, version_kind=kind, version_label=None,
                           artist=SimpleNamespace(name="The Rolling Stones"), artist_id=1, project_id=None,
                           project=None, spotify_url=None)


# ==================== the check ====================

@pytest.mark.parametrize("titles, release_kind, fits", [
    ([STUDIO_SINGLE, STUDIO_SINGLE], None, False),
    (["Out of Our Heads", None], None, False),
    ([STUDIO_SINGLE, LIVE_RELEASE], None, True),     # the recording found reads live
    (["Live at Leeds", None], None, True),           # a release titled as a live one
    (["Live in Gdańsk", None], None, True),
    (["Koncert w Jarocinie", None], None, True),
    (["Hits - Live", None], None, True),
    (["Get Yer Ya-Ya's Out!", None], VERSION_LIVE, True),  # the source says it's a live album
    (["Live Through This", None], None, False),      # a studio album starting with "Live"
    (["Live and Let Die", None], None, False),
])
def test_a_live_version_takes_only_a_live_release(titles, release_kind, fits):
    assert lookup_fits_version(_song(STUDIO_SINGLE, VERSION_LIVE), titles, release_kind) is fits


def test_other_songs_take_any_release():
    assert lookup_fits_version(_song(STUDIO_SINGLE), [STUDIO_SINGLE])
    assert lookup_fits_version(_song(STUDIO_SINGLE, VERSION_ACOUSTIC), [STUDIO_SINGLE])  # not in the set
    assert not lookup_fits_version(_song(STUDIO_SINGLE, VERSION_REMIX), [STUDIO_SINGLE])  # remixes need their own


def test_reads_as_version_needs_the_kind_asked():
    assert reads_as_version(LIVE_RELEASE, VERSION_LIVE)
    assert not reads_as_version("The Last Time (Remix)", VERSION_LIVE)
    assert not reads_as_version(None, VERSION_LIVE)


def test_musicbrainz_says_a_release_group_is_live(monkeypatch):
    monkeypatch.setattr(music_brainz_fetcher.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(music_brainz_fetcher.musicbrainzngs, "search_release_groups", lambda query, limit: {
        "release-group-list": [{"title": "Get Yer Ya-Ya's Out!", "first-release-date": "1970-09-04",
                                "primary-type": "Album", "secondary-type-list": ["Live"],
                                "artist-credit": [{"name": "The Rolling Stones"}]}]})

    result = music_brainz_fetcher.get_release_year("The Rolling Stones", "Get Yer Ya-Ya's Out!", is_single=False)

    assert (result.year, result.release_kind) == (1970, VERSION_LIVE)
    assert music_brainz_fetcher._release_kind({"secondary-type-list": []}) is None


# ==================== Years ====================

def _years(monkeypatch, songs, result, ticks=None, typed=""):
    """Run Years for `songs` with every lookup answering `result` (or
    `result(query)` for a function); the review
    ticks `ticks` (indexes; None = as proposed). Returns (edits, the review's
    choices as (title, checked), the lookups' keyword arguments)."""
    module = importlib.import_module(f"{_PKG}.fill_missing_years")
    edits, shown, calls = [], [], []
    monkeypatch.setattr(module, "close_global_driver", lambda: None)
    monkeypatch.setattr(module, "load_max_songs_per_fetch", lambda: 100)
    monkeypatch.setattr(module, "ask_yes_no", lambda q, **k: False)
    monkeypatch.setattr(module, "edit_db_entry", lambda song, category, value: edits.append((song.title, category, value)))
    monkeypatch.setattr(module, "_fill_years_known_in_database", lambda songs: [])
    monkeypatch.setattr(module, "load_discovery_modules", lambda kind: ["module"])
    monkeypatch.setattr(module, "release_year_from_stored_link", lambda songs: None)
    monkeypatch.setattr(module, "artist_match_synonyms", lambda artist: [])
    monkeypatch.setattr(module, "cyrillic_search_variant", lambda *a: None)
    monkeypatch.setattr(module, "recent_misses", lambda kind: set())
    monkeypatch.setattr(module, "remember_misses", lambda kind, ids: None)
    monkeypatch.setattr(module, "_finish", lambda touched: None)
    monkeypatch.setattr(module, "discover_release_year_result",
                        lambda artist, query, single, modules, synonyms, **kw: calls.append(kw) or (
                            result(query) if callable(result) else result))
    monkeypatch.setattr("utils.common.text_utils.copy_to_clipboard", lambda text: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": typed)

    def checkbox(message, choices):
        shown.extend((c.title, c.checked) for c in choices)
        picked = [i for i, c in enumerate(choices) if c.checked] if ticks is None else ticks
        return SimpleNamespace(ask=lambda: picked)

    monkeypatch.setattr(questionary, "checkbox", checkbox)
    module.fill_missing_years(songs, {})
    return edits, shown, calls


def test_a_live_version_given_the_studio_singles_year_goes_to_review_unticked(monkeypatch, capsys):
    edits, shown, calls = _years(monkeypatch, [_song(STUDIO_SINGLE, VERSION_LIVE)],
                                 YearDiscoveryResult(1965, matched_title=STUDIO_SINGLE))

    assert edits == []  # unticked, nothing typed: left without a year
    assert shown == [(f'The Rolling Stones - The Last Time: 1965  (the release "{STUDIO_SINGLE}" isn\'t live, '
                      "maybe the original's)", False)]
    assert calls[0]["held"] is False  # a live release's later year isn't dropped as a reissue's
    assert "of a release that isn't live" in capsys.readouterr().out


def test_the_owner_can_tick_a_held_live_year(monkeypatch):
    edits, _, _ = _years(monkeypatch, [_song(STUDIO_SINGLE, VERSION_LIVE)],
                         YearDiscoveryResult(1965, matched_title=STUDIO_SINGLE), ticks=[0])
    assert edits == [(STUDIO_SINGLE, "year", "1965")]


@pytest.mark.parametrize("result", [YearDiscoveryResult(2008, matched_title=LIVE_RELEASE),
                                    YearDiscoveryResult(2008, matched_title="Shine a Light", release_kind=VERSION_LIVE)])
def test_a_live_version_given_a_live_releases_year_saves_it(monkeypatch, result):
    edits, shown, _ = _years(monkeypatch, [_song(STUDIO_SINGLE, VERSION_LIVE)], result)
    assert edits == [(STUDIO_SINGLE, "year", "2008")]
    assert shown == []


def test_a_song_that_isnt_a_version_keeps_the_studio_year(monkeypatch):
    edits, shown, calls = _years(monkeypatch, [_song(STUDIO_SINGLE)], YearDiscoveryResult(1965, matched_title=STUDIO_SINGLE))
    assert edits == [(STUDIO_SINGLE, "year", "1965")]
    assert shown == []
    assert calls[0]["held"] is True


def test_an_albums_year_holds_only_its_live_versions(monkeypatch):
    live, studio = _song("Satisfaction", VERSION_LIVE, album="Out of Our Heads"), _song("Play with Fire", album="Out of Our Heads")
    edits, shown, _ = _years(monkeypatch, [live, studio], YearDiscoveryResult(1965, matched_title="Out of Our Heads"))

    assert edits == [("Play with Fire", "year", "1965")]
    assert [title for title, _ in shown] == [
        'The Rolling Stones - Out of Our Heads (Satisfaction): 1965  (the release "Out of Our Heads" isn\'t live, '
        "maybe the original's)"]


def test_unattended_a_held_live_year_is_never_saved(monkeypatch):
    # the built-in "years_unsure" rule on and an owner's rule taking anything:
    # the YouTube date of the studio song is taken, the live version's year not
    monkeypatch.setattr(prompts, "rule_on", lambda rule: True)
    monkeypatch.setattr(prompts, "user_rule_taking", lambda rule, facts: "anything")
    live, studio = _song(STUDIO_SINGLE, VERSION_LIVE), _song("Heart of Stone")
    results = {STUDIO_SINGLE: YearDiscoveryResult(1965, matched_title=STUDIO_SINGLE),
               "Heart of Stone": YearDiscoveryResult(1964, matched_title="Heart of Stone", needs_review=True,
                                                     source_url="https://youtu.be/x")}
    with prompts.unattended():
        edits, shown, _ = _years(monkeypatch, [live, studio], results.get)

    assert edits == [("Heart of Stone", "year", "1964")]
    assert shown == []  # nothing asked


# ==================== Albums ====================

def _albums(monkeypatch, songs, match, ticks=()):
    """Run Albums for `songs` with every lookup answering `match`; the review
    ticks `ticks`. Returns (album edits, the review's labels, the pairs whose
    album details were saved)."""
    module = importlib.import_module(f"{_PKG}.fill_missing_albums")
    edits, shown, details = [], [], []
    monkeypatch.setattr(module, "close_global_driver", lambda: None)
    monkeypatch.setattr(module, "load_max_songs_per_fetch", lambda: 100)
    monkeypatch.setattr(module, "ask_yes_no", lambda q, **k: False)
    monkeypatch.setattr(module, "submit_global_database_session", lambda: None)
    monkeypatch.setattr(module, "edit_db_entry", lambda song, category, value: edits.append((song.title, category, value)))
    monkeypatch.setattr(module, "load_discovery_modules", lambda: [])
    monkeypatch.setattr(module, "AlbumTrackCache", lambda songs: None)
    monkeypatch.setattr(module, "recent_misses", lambda kind: set())
    monkeypatch.setattr(module, "remember_misses", lambda kind, ids: None)
    monkeypatch.setattr(module, "discover_album", lambda song, modules, cache=None: match)
    monkeypatch.setattr(module, "_save_album_details", lambda saved, modules=(): details.extend(saved))

    def checkbox(message, choices):
        shown.extend("".join(text for _, text in c.title) for c in choices)
        assert not any(c.checked for c in choices)
        return SimpleNamespace(ask=lambda: list(ticks))

    monkeypatch.setattr(questionary, "checkbox", checkbox)
    module.fill_missing_albums(songs)
    return edits, shown, details


def test_a_live_version_given_the_studio_single_goes_to_review_unticked(monkeypatch, capsys):
    song = _song(STUDIO_SINGLE, VERSION_LIVE)
    edits, shown, details = _albums(monkeypatch, [song], AlbumMatch(STUDIO_SINGLE, year=1965,
                                                                    matched_title=STUDIO_SINGLE))

    assert edits == [] and details == []  # neither the album nor its 1965
    assert len(shown) == 1 and "album: The Last Time (1965)" in shown[0]
    assert "isn't a live release" in capsys.readouterr().out


def test_a_ticked_live_versions_album_is_saved_with_its_details(monkeypatch):
    match = AlbumMatch(STUDIO_SINGLE, year=1965, matched_title=STUDIO_SINGLE)
    edits, _, details = _albums(monkeypatch, [_song(STUDIO_SINGLE, VERSION_LIVE)], match, ticks=[0])
    assert edits == [(STUDIO_SINGLE, "album", STUDIO_SINGLE)]
    assert [m for _, m in details] == [match]


@pytest.mark.parametrize("match", [AlbumMatch("Shine a Light", matched_title=LIVE_RELEASE),
                                   AlbumMatch("Get Yer Ya-Ya's Out!", release_kind=VERSION_LIVE),
                                   AlbumMatch("Live at the Beacon Theatre")])
def test_a_live_version_given_a_live_album_saves_it(monkeypatch, match):
    edits, shown, _ = _albums(monkeypatch, [_song(STUDIO_SINGLE, VERSION_LIVE)], match)
    assert edits == [(STUDIO_SINGLE, "album", match.album)]
    assert shown == []


def test_a_song_that_isnt_a_version_keeps_the_studio_album(monkeypatch):
    edits, shown, _ = _albums(monkeypatch, [_song(STUDIO_SINGLE)], AlbumMatch(STUDIO_SINGLE, matched_title=STUDIO_SINGLE))
    assert edits == [(STUDIO_SINGLE, "album", STUDIO_SINGLE)]
    assert shown == []


def test_unattended_a_live_versions_studio_album_is_never_saved(monkeypatch):
    monkeypatch.setattr(prompts, "rule_on", lambda rule: True)
    monkeypatch.setattr(prompts, "user_rule_taking", lambda rule, facts: "anything")
    live, studio = _song(STUDIO_SINGLE, VERSION_LIVE), _song("Heart of Stone")
    with prompts.unattended():
        edits, shown, _ = _albums(monkeypatch, [live, studio], AlbumMatch("Out of Our Heads", matched_title="x"))
    assert edits == [("Heart of Stone", "album", "Out of Our Heads")]
    assert shown == []  # nothing asked
