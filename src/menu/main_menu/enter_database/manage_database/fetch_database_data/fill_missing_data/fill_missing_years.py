import re
import random
from collections import defaultdict
from dataclasses import replace
from utils.common.title_patterns import FEAT_WORD, YEAR
from datetime import date
from utils.common.output import RESET, YELLOW, review_header
from utils.common.automation_settings import recent_misses, remember_misses
from utils.common.debug import flog
from utils.ui.prompts import ask_typed, ask_yes_no, decided, stop_requested, tick_items, unattended_now
from utils.database.database_getter import get_known_album_year, get_songs_missing_year
from utils.database.database_management import edit_db_entry
from utils.database.datatables import Artist, Song
from utils.database.tags_management import add_tag_to_songs, song_ids_with_tag
from utils.common.debug import slog
from menu.song_actions import edit_songs_menu
from utils.database.database_sessions import require_global_database_sessions, submit_global_database_session
from utils.common.selenium_sessions import close_global_driver
from utils.discoveries.discoveries_manager import (
    discover_release_year_result,
    load_discovery_modules,
    release_year_from_stored_link,
)
from utils.discoveries.discovery_modules.music_brainz_fetcher import first_appearance_year
from utils.database.fetch_data_settings import load_max_songs_per_fetch
from utils.discoveries.discovery_settings import YEARS
from utils.database.latin_names import artist_match_synonyms
from config.constants import (SINGLES_ALBUM, MANUAL_YEAR_SAMPLE_SONGS, MUSICBRAINZ_REISSUE_AFTER_YEARS,
                              SONG_KIND_FULL_ALBUM, YEAR_CHECKED_TAG)
from utils.common.normalizer import compare, normalize
from utils.common.script_utils import cyrillic_search_variant


def fill_missing_years(songs: list | None = None, entries: dict | None = None):
    """The year a song first came out, in two steps:
    1. the songs without one - every such song, or only those of `songs` (an
       import's, filled in right after it); `entries` (song id -> the
       SongEntry it was read from, ImportResult.entries): a year its title
       had ("Legenda (1991)") is offered in the unsure-years review when no
       module finds one;
    2. for the whole catalog only (no `songs`): the stored years not checked
       yet, held against MusicBrainz's first appearance
       (_check_stored_years()), in what step 1 left of max-songs-per-fetch."""
    touched, looked_up = _fill_empty_years(songs, entries)
    if songs is None:
        touched += _check_stored_years(load_max_songs_per_fetch() - looked_up, {song.id for song in touched})
    _finish(touched)


def _no_album(song) -> bool:
    return not song.album or song.album == SINGLES_ALBUM


def _fill_empty_years(songs, entries) -> tuple[list, int]:
    """Step 1. Returns the songs that got a year and how many songs were
    looked up on the internet."""
    if songs is None:
        songs_objects = get_songs_missing_year()
    else:
        songs_objects = [song for song in songs if song.year is None]
    slog(songs_objects)

    if not songs_objects:
        print("No songs with a missing year found")
        return [], 0

    # Free, offline first step: an album whose year is already on another of
    # its songs gets that year, before the per-fetch cap so it never uses
    # up a slot meant for a network lookup.
    touched_songs = _fill_years_known_in_database(songs_objects)
    songs_objects = [song for song in songs_objects if song.year is None]
    if unattended_now():  # the automation: not the songs it found nothing for lately
        missed = recent_misses("years")
        songs_objects = [song for song in songs_objects if song.id not in missed]
    if not songs_objects:
        return touched_songs, 0

    max_songs = load_max_songs_per_fetch()
    if len(songs_objects) > max_songs:
        print(f"Found {len(songs_objects)} songs with missing years, processing the first {max_songs} (see Settings to change this).")
        songs_objects = songs_objects[:max_songs]

    # Group songs sharing a real album so the year is looked up once per
    # album and then written to every song in that group. A song with no
    # album is looked up as a single - but a project's recording (Albums
    # skips them) by its project as the album: its title alone finds the
    # studio original ("Liūdni slibinai - Pieno Fabrikėlis", 2012, not its
    # Kaunas Acoustics take), the project the edition ("Nirvana - MTV
    # Unplugged", 1994; "Lao Che - Męskie Granie", 2013 - but an artist may
    # have played several editions, so that year is always reviewed).
    groups = defaultdict(list)
    projects = defaultdict(list)
    singles = []
    for song in songs_objects:
        if song.project_id is not None and _no_album(song):
            projects[(song.artist.name, song.project.name)].append(song)
        elif _no_album(song):
            singles.append(song)
        else:
            groups[(song.artist.name, song.album)].append(song)

    # A one-song "album" named after the song ("Venture" / "Venture") is a
    # single released under its own title - look it up as one, so it gets
    # the single-only lookups (YouTube, the "A x B" collab retry).
    for key, songs in list(groups.items()):
        # "Lottery ft. LU KALA" is still the "Lottery" single
        if len(songs) == 1 and (compare(songs[0].album, songs[0].title)
                                or compare(songs[0].album, _strip_guest_credit(songs[0].title))):
            singles.append(songs[0])
            del groups[key]

    # A version naming its year ("Malinowy chruśniak + Polsko" (Live,
    # Fryderyki 2018)) is that performance: a lookup of its title finds the
    # studio single's year instead (2017, 2026-11-21) - offered for review
    dated_versions = [song for song in singles if _version_year(song)]
    singles = [song for song in singles if song not in dated_versions]

    print("Preparing fetch modules . . . ")

    # (label, year_or_None, [songs]) collected here, paired by index with
    # what was queried so the "fill up manually" step can show the user
    # what to search for.
    results = []
    # (label, year, [songs], source_url) for years a module flagged
    # needs_review (YouTube upload dates) - shown to the user before
    # anything is written.
    review_results = []
    looked_at = []  # the songs whose year was looked up (not those left by a stop)
    for song in dated_versions:
        print(f"Year {YELLOW}{_version_year(song)}{RESET} from the version of {YELLOW}{song.artist.name} - "
              f"{song.title}{RESET} (to review at the end)")
        review_results.append((f"{song.artist.name} - {song.title}", _version_year(song), [song],
                               f'the version "{song.version_label}"'))

    try:
        modules = load_discovery_modules(YEARS)
        if not modules:
            print("No discovery modules support looking up a release year yet (see Settings -> Discovery modules).")
            return touched_songs, 0

        # (label, artist, album / single's title / project, is it a single, its songs, what else the
        # lookup takes, is it a project's)
        lookups = [(f"{artist_name} - {album_name}", artist_name, album_name, False, songs,
                    {"album_songs": songs, "alt_script_query": cyrillic_search_variant(
                        album_name, [artist_name, *(s.title for s in songs)])}, False)
                   for (artist_name, album_name), songs in groups.items()]
        lookups += [(f"{artist_name} - {project} ({', '.join(s.title for s in songs)})", artist_name, project,
                     False, songs, {"album_songs": songs, "held": False}, True)
                    for (artist_name, project), songs in projects.items()]
        lookups += [(f"{song.artist.name} - {song.title}", song.artist.name, song.title, True, [song],
                     {"song": song, "alt_script_query": cyrillic_search_variant(
                         song.title, [song.artist.name, song.album])}, False)
                    for song in singles]
        try:
            for label, artist_name, name, single, songs, extra, project in lookups:
                if stop_requested():  # the automation: its time is up, or the app waits
                    print("\nStopped - the years found so far are saved.")
                    break
                looked_at += songs
                print()
                print(f"Checking release year for {'single ' if single else ''}{YELLOW}{label}{RESET}")
                result = (release_year_from_stored_link(songs)
                          or discover_release_year_result(artist_name, name, single, modules,
                                                          artist_match_synonyms(songs[0].artist), **extra))
                if project and result is not None and not result.needs_review:
                    result = replace(result, needs_review=True, source_url=(
                        f'the release "{result.matched_title}"' if result.matched_title else f"a release of {name}"))
                _collect(label, result, songs, results, review_results)
        except KeyboardInterrupt:
            # The lookups are slow (a browser per module): keep what was found
            print("\nStopped - the years found so far are saved.")
    finally:
        close_global_driver()

    results = _years_from_titles(results, entries, review_results)
    no_year_entries = []
    for label, year, songs in results:
        if year is not None:
            for song in songs:
                edit_db_entry(song, "year", str(year))
                touched_songs.append(song)
        else:
            no_year_entries.append((label, songs))

    touched_songs += _review_unsure_years(review_results)

    if no_year_entries:
        total_missing_songs = sum(len(songs) for _, songs in no_year_entries)
        confirmation = ask_yes_no(f"Unable to find {total_missing_songs} release year(s) across {len(no_year_entries)} album/single group(s). "
            f"Would you like to fill them up manually?")
        if confirmation:
            for label, songs in no_year_entries:
                year = _ask_year(label, "New year (put nothing to cancel): ", label + _sample_titles(label, songs))
                if year is not None:
                    for song in songs:
                        edit_db_entry(song, "year", str(year))
                        touched_songs.append(song)

    if unattended_now():
        remember_misses("years", [song.id for song in looked_at if song.year is None])
    return touched_songs, len(looked_at)


def _sample_titles(label: str, songs) -> str:
    """ " (title, title, title)" - up to MANUAL_YEAR_SAMPLE_SONGS random songs
    of an album, as a hint which album it is. Empty for a single or a
    project's recordings, whose label already names them."""
    titles = [song.title for song in songs if song.title not in label]
    if not titles:
        return ""
    return f" ({', '.join(random.sample(titles, min(MANUAL_YEAR_SAMPLE_SONGS, len(titles))))})"


# Only a guest credit - unlike truncate_at_word(), which also cuts at a comma
# ("Dziękuję, że jesteś" isn't a single from the album "Dziękuję")
_GUEST_CREDIT = re.compile(r"\s*[(\[]?\s*\b" + FEAT_WORD + r"\s.*$", re.IGNORECASE)


def _strip_guest_credit(title: str) -> str:
    return _GUEST_CREDIT.sub("", title)


def _finish(touched_songs) -> None:
    if touched_songs:
        confirmation = ask_yes_no("Do you want to edit some of the changes manually?")
        if confirmation:
            edit_songs_menu(touched_songs)

    submit_global_database_session()


def _fill_years_known_in_database(songs) -> list:
    """Write a year already recorded on another song of the same album (see
    get_known_album_year()) to these songs, and to a project's recording
    with no album the year its artist names ("Męskie Granie Orkiestra 2019",
    that edition's band). Returns the songs that got one."""
    groups = defaultdict(list)
    touched = []
    for song in songs:
        if not _no_album(song):
            groups[(song.artist_id, normalize(song.album))].append(song)
        elif song.project_id is not None and (named := re.search(rf"\b({YEAR})\b", song.artist.name)):
            print(f"Year {YELLOW}{named[1]}{RESET} for {YELLOW}{song.artist.name} - {song.title}{RESET} "
                  f"from the artist's name")
            edit_db_entry(song, "year", named[1])
            touched.append(song)

    for (artist_id, _), group in groups.items():
        year = get_known_album_year(artist_id, group[0].album)
        if year is None:
            continue
        print(f"Found year {YELLOW}{year}{RESET} for {YELLOW}{group[0].artist.name} - {group[0].album}{RESET} "
              f"in the database ({len(group)} song(s))")
        for song in group:
            edit_db_entry(song, "year", str(year))
            touched.append(song)
    return touched


_VERSION_YEAR_RE = re.compile(r"\b" + YEAR + r"\b")


def _version_year(song) -> int | None:
    """The year a song's version label names ("Live, Fryderyki 2018"), else None."""
    match = _VERSION_YEAR_RE.search(getattr(song, "version_label", None) or "")
    return int(match.group()) if match else None


def _years_from_titles(results, entries, review_results) -> list:
    """`results` without the groups no module found a year for whose songs'
    titles name one (SongEntry.hints, all the same year) - those go to
    `review_results`, the title shown as the source."""
    if not entries:
        return results
    left = []
    for label, year, songs in results:
        hinted = [(entry.hints.year, entry) for song in songs
                  if (entry := entries.get(song.id)) is not None and entry.hints.year]
        if year is None and hinted and len({y for y, _ in hinted}) == 1:
            title_year, entry = hinted[0]
            print(f"Year {YELLOW}{title_year}{RESET} from the title for {YELLOW}{label}{RESET} (to review at the end)")
            review_results.append((label, title_year, songs, f'the title "{entry.raw_text or entry.title}"'))
        else:
            left.append((label, year, songs))
    return left


def _collect(label, result, songs, results, review_results) -> None:
    if result is None:
        print(f"Couldn't find release year for {YELLOW}{label}{RESET}")
        results.append((label, None, songs))
    elif result.needs_review:
        print(f"Found year {YELLOW}{result.year}{RESET} for {YELLOW}{label}{RESET} (unsure, to review at the end)")
        review_results.append((label, result.year, songs, result.source_url))
    else:
        print(f"Found year {YELLOW}{result.year}{RESET} for {YELLOW}{label}{RESET} ({len(songs)} song(s))")
        results.append((label, result.year, songs))


# A year typed by hand: no recording is older, and nothing is out yet after next year
_FIRST_YEAR = 1900


def _ask_year(label: str, question: str, shown: str) -> int | None:
    """A year typed for `label`; None when skipped or not a year."""
    typed = ask_typed(label, question, shown)
    if typed is None:
        return None
    year = _typed_year(typed)
    if year is None:
        print(f"'{typed}' is not a year between {_FIRST_YEAR} and {date.today().year + 1}, skipped {label}")
    return year


def _typed_year(text: str) -> int | None:
    """`text` as a year from _FIRST_YEAR to next year, else None ("0", "20245")."""
    if not text.isdigit():
        return None
    year = int(text)
    return year if _FIRST_YEAR <= year <= date.today().year + 1 else None


def _review_unsure_years(review_results) -> list:
    """List every year a module flagged needs_review (a YouTube upload date
    is often not the release date), a title named or a project's release
    gave, all ticked: like every other checkbox
    review, the ticked ones are saved. Unticked ones ask for the right year
    (empty = leave the song without one). Returns the songs that got a year."""
    if not review_results:
        return []

    review_header(f"REVIEW: {len(review_results)} unsure release year(s), taken from a YouTube video's date "
                  f"or a title, or a project's release")
    selected = tick_items("Save the ticked years; untick a wrong one to type it (space toggles, enter confirms):",
                          review_results,
                          lambda found: f"{found[0]}: {found[1]}" + (f"  ({found[3]})" if found[3] else ""),
                          checked=True, rule="years_unsure",
                          facts=lambda found: {"year": found[1], "songs": len(found[2]),
                                               "from_title": str(found[3] or "").startswith("the title"),
                                               "from_youtube": "youtu" in str(found[3] or "").lower(),
                                               "project": found[2][0].project_id is not None})
    if selected is None:
        print("Aborted, none of these years saved.")
        return []

    touched = []
    for found in review_results:
        label, year, songs, _ = found
        # Unattended, nothing is typed: an untaken year leaves the song without one
        if not any(found is s for s in selected):
            year = _ask_year(label, "Right year (put nothing to leave it empty): ", f"{label} (found {year})")
            if year is None:
                continue
        for song in songs:
            edit_db_entry(song, "year", str(year))
            touched.append(song)
    return touched


# What the automation's step 2 left unasked (no rule of the owner's took it)
_STORED_YEARS = "stored_years"


def _check_stored_years(room: int, skip: set) -> list:
    """Step 2: a stored year more than MUSICBRAINZ_REISSUE_AFTER_YEARS after
    the song first appeared on any release MusicBrainz knows is a reissue's
    or a compilation's (très.b - "Ola" had the year of Męskie Granie's 2013
    compilation, MusicBrainz knows it from 2007) - offered in one checkbox
    list, none ticked: a trial on 200 songs (2026-11-20) gave 6, about half
    right - MusicBrainz also dates a live take, a later re-recording ("Re:Re:"
    for an anime, 2016) or a mis-dated edition earlier. A song MusicBrainz
    first knows later than ours, or not at all, stays as it is: then
    MusicBrainz lacks the original (Maanam's 1981 single "Cykady na
    Cykladach"), not we.

    Grouped like step 1 (artist + album, a single or a song with no album
    alone), but an album gets one request (its first song asked) and one
    change for all its songs only while they share one stored year. One
    with several years is checked song by song, each changed on its own:
    then the album text is usually wrong for some of its songs (Kasabian's
    "Club Foot" 2004 under "West Ryder Pauper Lunatic Asylum" with "Fire"
    2009 - asked by "Club Foot", all three got 2004). The songs not checked yet (YEAR_CHECKED_TAG in tag.db) apart from `skip`
    (step 1's), whole groups up to `room` songs a run (at least one group).
    Versions, a project's recordings and full albums are left out (their
    titles would find the original's recordings); a song MusicBrainz
    couldn't be asked about stays unchecked. Returns the songs changed."""
    if room <= 0:
        print("The stored years are checked next time: this run's max songs per fetch went on the missing ones.")
        return []
    session, _ = require_global_database_sessions()
    checked = song_ids_with_tag(YEAR_CHECKED_TAG) | skip
    if unattended_now():  # the automation: not the changes no rule took lately
        checked |= recent_misses(_STORED_YEARS)
    groups = defaultdict(list)
    for song in (session.query(Song).join(Song.artist).filter(Song.year.isnot(None))
                 .order_by(Artist.name, Song.album, Song.title)):
        if (song.id not in checked and not song.version_kind and song.project_id is None
                and song.kind != SONG_KIND_FULL_ALBUM):
            groups[song.id if _no_album(song) else (song.artist.name, song.album)].append(song)
    groups = [part for group in groups.values()
              for part in ([group] if len({song.year for song in group}) == 1 else [[song] for song in group])]
    if not groups:
        print("Every stored year has been checked against MusicBrainz.")
        return []
    batch = []
    for group in groups:
        if batch and sum(map(len, batch)) + len(group) > room:
            break
        batch.append(group)
    total = sum(map(len, groups))
    print(f"\nChecking stored years against MusicBrainz's first release: {total} song(s) not checked yet"
          + (f", the first {sum(map(len, batch))} now (see Settings to change this)." if len(batch) < len(groups)
             else "."))

    proposals, looked_at = [], []  # proposals: (songs, MusicBrainz's first year)
    try:
        for position, group in enumerate(batch, 1):
            if stop_requested():
                break
            song = group[0]
            try:
                first = first_appearance_year(song.artist.name, song.title, raise_errors=True)
            except Exception as exc:  # no network, MusicBrainz busy: asked again next time
                flog(f"[Years] couldn't ask MusicBrainz about '{song.artist.name} - {song.title}': {exc}")
                print(f"[{position}] {song.artist.name} - {song.title}: MusicBrainz didn't answer")
                continue
            looked_at += group
            if first is not None and song.year > first + MUSICBRAINZ_REISSUE_AFTER_YEARS:
                proposals.append((group, first))
                print(f"[{position}] {_stored_label((group, first))}")
    except KeyboardInterrupt:
        print("\nStopped - the songs checked so far are kept.")
    asked = {song.id for group, _ in proposals for song in group}
    picked = []
    if proposals:
        review_header(f"REVIEW: {len(proposals)} stored year(s) later than the song's first release",
                      "MusicBrainz knows these songs from an earlier release: the stored year is most likely "
                      "a reissue's or a compilation's. An album's songs sharing one year are changed together.",
                      "None ticked: MusicBrainz is often wrong too - tick the ones to change; songs left "
                      "unticked aren't offered again.")
        picked = tick_items("Tick the years to change (space toggles, enter confirms):", proposals, _stored_label,
                            checked=False, rule=_STORED_YEARS, facts=_stored_facts)
        if picked is None:
            print("Aborted, no stored year changed; the songs without a question are marked as checked.")
            picked, proposals = [], []
    changed = []
    for group, first in picked:
        for song in group:
            edit_db_entry(song, "year", str(first))
            changed.append(song)
    answered = decided(proposals, picked)
    if unattended_now():
        remember_misses(_STORED_YEARS, asked - {song.id for group, _ in answered for song in group})
    add_tag_to_songs([song.id for song in looked_at if song.id not in asked]
                     + [song.id for group, _ in answered for song in group], YEAR_CHECKED_TAG)
    print(f"Checked {len(looked_at)} stored year(s), changed {len(changed)}.")
    return changed


def _stored_label(proposal) -> str:
    group, first = proposal
    song = group[0]
    if len(group) == 1:
        album = f" [{song.album}]" if song.album else ""
        return f"{song.artist.name} - {song.title}{album}:  {song.year}  ->  {first}  (MusicBrainz's first release)"
    return (f"{song.artist.name} - {song.album} ({len(group)} song(s), asked by \"{song.title}\"):  {song.year}  ->  "
            f"{first}  (MusicBrainz's first release)")


def _stored_facts(proposal) -> dict:
    group, first = proposal
    stored = group[0].year
    return {"stored_year": stored, "first_year": first, "years_later": stored - first, "songs": len(group),
            "album": not _no_album(group[0])}
