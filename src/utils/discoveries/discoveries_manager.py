from utils.common.text_utils import (
    truncate_at_word,
    is_blacklisted_album,
    is_soundtrack_artist,
    has_singles_marker,
    similarity,
    scaled_similarity_threshold,
    title_resembles,
    resembles_across_scripts,
)
from utils.discoveries.discovery_result import AlbumTrack, DiscoveryResult, YearDiscoveryResult
from utils.discoveries.discovery_settings import (
    reconcile_discovery_config,
    reconcile_year_discovery_config,
)
from utils.discoveries.discovery_stats import (
    record_invocation,
    record_success,
    record_year_invocation,
    record_year_success,
)
from config.constants import SPELLING_CHECK_THRESHOLD, MIN_PLAUSIBLE_RELEASE_YEAR, SINGLES_ALBUM
from datetime import datetime
import ast
import inspect
import importlib.util
import sys
from pathlib import Path
from utils.common.debug import flog, slog
from utils.common.normalizer import name_key, normalize
from utils.database.song_artists import song_all_artists, song_main_artists
from utils.database.latin_names import artist_match_synonyms
from utils.database.artist_splitting import propose_artist_split
from utils.common.script_utils import cyrillic_search_variant, has_non_latin
from utils.common.romanize import suggest_latin
from config.constants import MULTI_ARTIST_MAIN_SEPARATOR
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable
import re

def split_synonyms(synonyms: str | None) -> list[str]:
    """The artists.synonyms column is a plain comma-separated list."""
    return [s.strip() for s in (synonyms or "").split(",") if s.strip()]


def _discovery_modules_dir() -> Path:
    return Path(__file__).parent / "discovery_modules"


def _import_module(script_path: Path):
    """Import a single discovery module file by path."""
    module_id = script_path.stem
    spec = importlib.util.spec_from_file_location(module_id, script_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_id] = module
    spec.loader.exec_module(module)
    return module


def _read_module_name(script_path: Path) -> str:
    """Extract a module's ``MODULE_NAME`` by statically parsing the file, without
    importing (executing) it. The Settings menu only needs the display name;
    importing every module just to read one string would run all their
    top-level side effects (selenium, network clients, musicbrainzngs
    useragent setup, ...) — for disabled modules too. Falls back to the
    filename stem if the constant is missing or the file can't be parsed."""
    try:
        tree = ast.parse(script_path.read_text(encoding="utf-8"), filename=str(script_path))
    except (OSError, SyntaxError):
        return script_path.stem

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "MODULE_NAME" for t in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value

    return script_path.stem


def _module_defines_function(script_path: Path, func_name: str) -> bool:
    """Statically check whether a module defines a top-level function named
    func_name, without importing it (same reasoning as _read_module_name:
    importing every file just to check for an optional function would run
    disabled/unrelated modules' import-time side effects). Used to find
    which discovery modules implement get_release_year(), since not every
    album fetcher supports year lookups."""
    try:
        tree = ast.parse(script_path.read_text(encoding="utf-8"), filename=str(script_path))
    except (OSError, SyntaxError):
        return False

    return any(
        isinstance(node, ast.FunctionDef) and node.name == func_name
        for node in tree.body
    )


def load_discovery_modules():
    """Import and return the *enabled* discovery modules, in the user's
    configured order (see discovery_settings.py), ready to be passed to
    discover_album_name()."""
    module_files = {p.stem: p for p in _discovery_modules_dir().glob("*.py")}
    config = reconcile_discovery_config(module_files.keys())

    modules = []
    for module_id in config["order"]:
        if not config["enabled"].get(module_id, True):
            continue
        module = _import_module(module_files[module_id])
        module_name = getattr(module, "MODULE_NAME", module_id)  # fallback to filename if missing
        modules.append((module_id, module_name, module))

    return modules


def load_all_discovery_modules_metadata():
    """Return [(module_id, display_name), ...] for every discovery module file
    (enabled or not), in the user's configured order, for use by the Settings
    menu (enable/disable + reorder screens).

    Display names are read by statically parsing each file's ``MODULE_NAME``
    (see _read_module_name) rather than importing the modules, so opening the
    Settings menu doesn't run every module's import-time side effects."""
    module_files = {p.stem: p for p in _discovery_modules_dir().glob("*.py")}
    config = reconcile_discovery_config(module_files.keys())

    return [
        (module_id, _read_module_name(module_files[module_id]))
        for module_id in config["order"]
    ]


def _year_capable_module_files() -> dict:
    """Module files (by id) that statically define get_release_year() -
    the subset usable for "Fill missing data -> Years", distinct from the
    full set of album fetchers."""
    module_files = {p.stem: p for p in _discovery_modules_dir().glob("*.py")}
    return {
        module_id: path
        for module_id, path in module_files.items()
        if _module_defines_function(path, "get_release_year")
    }


def load_year_discovery_modules():
    """Import and return the *enabled* year-capable discovery modules, in
    the user's configured order (see discovery_settings.py), ready to be
    passed to discover_release_year()."""
    module_files = _year_capable_module_files()
    config = reconcile_year_discovery_config(module_files.keys())

    modules = []
    for module_id in config["order"]:
        if not config["enabled"].get(module_id, True):
            continue
        module = _import_module(module_files[module_id])
        module_name = getattr(module, "MODULE_NAME", module_id)
        modules.append((module_id, module_name, module))

    return modules


def load_all_year_discovery_modules_metadata():
    """Return [(module_id, display_name), ...] for every year-capable
    discovery module file (enabled or not), in the user's configured order,
    for the Settings menu (enable/disable + reorder screens for Years)."""
    module_files = _year_capable_module_files()
    config = reconcile_year_discovery_config(module_files.keys())

    return [
        (module_id, _read_module_name(module_files[module_id]))
        for module_id in config["order"]
    ]


def _matched_artist_resembles(queried_artist: str, matched_artist: str, credited_artists: list[str] | None = None) -> bool:
    """Does the artist a module says it matched resemble what we searched for?

    `credited_artists` is only passed for a multi-artist song (all its
    artists, see song_artists.py). We query with the primary artist alone, but
    services often credit the whole collab in one field ("Sw@da, Maxim &
    Niczos") - plain similarity to "Sw@da" then fails, so also accept a
    matched field that contains any credited artist as whole words."""
    threshold = scaled_similarity_threshold(queried_artist, matched_artist, SPELLING_CHECK_THRESHOLD)
    if similarity(queried_artist, matched_artist) >= threshold:
        return True
    # Same name once punctuation, a bracketed native-script name, a leading
    # "The" and spacing are dropped: "Cody・Lee(李)" is "Cody Lee", "The S.O.S.
    # Band" is "SOS Band", though plain similarity says 0.70-0.74.
    queried_key = name_key(queried_artist)
    if queried_key and queried_key == name_key(matched_artist):
        return True
    # "Ani Lorak" vs "Ани Лорак", "Ані Лорак" vs "Ани Лорак"
    if resembles_across_scripts(queried_artist, matched_artist):
        return True
    # A service crediting several artists in one field ("Paulina Przybysz, Ten
    # Typ Mes, Albo Inaczej") for a song stored under just one of them. Split
    # by name, not substring: "Ariel Pink" mustn't pass for "Ariel".
    proposal = propose_artist_split(matched_artist)
    if proposal and any(_matched_artist_resembles(queried_artist, name) for name in proposal.names):
        return True
    if credited_artists:
        matched_padded = f" {normalize(matched_artist)} "
        return any(
            normalize(name) and f" {normalize(name)} " in matched_padded
            for name in credited_artists
        )
    return False


def _validate_result(result, queried_artist: str, queried_title: str, module_name: str,
                     credited_artists: list[str] | None = None) -> str | None:
    """Defensively sanity-check what a discovery module handed back.

    A module can be crashy, return the wrong type, return a blacklisted
    placeholder, or (the bug this exists to catch) confidently return an
    album that belongs to a completely different song. This does not trust
    the module's own internal verification — it re-checks everything itself
    so a poorly-written module can't silently poison the result.
    """
    if result is None:
        return None

    if isinstance(result, DiscoveryResult):
        album, matched_title, matched_artist = result.album, result.matched_title, result.matched_artist
    elif isinstance(result, str):
        album, matched_title, matched_artist = result, None, None
    else:
        flog(f"[{module_name}] returned unexpected type {type(result).__name__}, discarding")
        return None

    if not isinstance(album, str) or not album.strip():
        flog(f"[{module_name}] returned an empty/non-string album, discarding")
        return None
    album = album.strip()

    if is_blacklisted_album(album, queried_artist):
        flog(f"[{module_name}] returned blacklisted album '{album}', discarding")
        return None

    # If the module told us what it actually matched, independently verify
    # that resembles what we searched for. This is the check that catches a
    # module confidently returning data for the wrong song.
    if matched_title:
        threshold = scaled_similarity_threshold(queried_title, matched_title, SPELLING_CHECK_THRESHOLD)
        title_sim = similarity(queried_title, matched_title)
        if not title_resembles(queried_title, matched_title):
            flog(f"[{module_name}] matched title '{matched_title}' doesn't resemble query "
                 f"'{queried_title}' (sim={title_sim:.2f} < {threshold:.2f}), discarding")
            return None

    if matched_artist and not _matched_artist_resembles(queried_artist, matched_artist, credited_artists):
        flog(f"[{module_name}] matched artist '{matched_artist}' doesn't resemble query "
             f"'{queried_artist}' (sim={similarity(queried_artist, matched_artist):.2f}), discarding")
        return None

    return album


@dataclass
class AlbumMatch:
    """An album found for a song, with the song's place on it when the
    module knew it (None = unknown; disc None also means a one-disc album).
    load_tracks: the module's tracklist loader, see DiscoveryResult."""
    album: str
    track_number: int | None = None
    disc_number: int | None = None
    load_tracks: Callable[[], list[AlbumTrack]] | None = None


def _call_module(module, module_id: str, module_name: str, artist: str, title: str,
                 credited_artists: list[str] | None = None) -> str | None:
    """Run a module's get_album_name, isolated from crashes and bad output.

    Counts as one invocation for the Statistics menu even if this is a retry
    of the same module with untruncated text or an artist synonym -- each
    such retry is a real call to get_album_name().
    """
    match = _call_module_match(module, module_id, module_name, artist, title, credited_artists)
    return match.album if match else None


def _call_module_match(module, module_id: str, module_name: str, artist: str, title: str,
                       credited_artists: list[str] | None = None) -> AlbumMatch | None:
    """_call_module() keeping what else the module reported (track position,
    tracklist loader)."""
    record_invocation(module_id)
    try:
        result = module.get_album_name(artist, title)
    except Exception as e:
        flog(f"[{module_name}] crashed while looking up '{artist} - {title}': {e}")
        print(f"{module_name} failed unexpectedly, skipping it for this song")
        return None
    if result is None:
        flog(f"[{module_name}] found no album for '{artist} - {title}'")
    album = _validate_result(result, artist, title, module_name, credited_artists)
    if not album:
        return None
    record_success(module_id)
    if not isinstance(result, DiscoveryResult):
        return AlbumMatch(album)
    return AlbumMatch(album, _positive_int(result.track_number), _positive_int(result.disc_number),
                      result.load_tracks if callable(result.load_tracks) else None)


def _positive_int(value) -> int | None:
    """A module's track/disc number, if it's a believable one."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


# A remaster annotation doesn't make another song of a track: "Dust Of
# Harmonies (REMASTERED)" on the album is the stored "Dust of Harmonies"
_REMASTER_RE = re.compile(
    r"\s*(?:[(\[][^)\]]*\bremaster(?:ed)?\b[^)\]]*[)\]]"
    r"|-\s*(?:\d{4}\s+)?(?:digital(?:ly)?\s+)?remaster(?:ed)?(?:\s+\d{4})?(?:\s+version)?\s*$)",
    re.IGNORECASE,
)


def _track_key(title: str) -> str:
    """Key matching a stored title with an album's track: the duplicate-song
    key (feat. credits and junk brackets dropped, a remix or live bracket
    kept) without remaster annotations. Equal keys only - a similar title
    may be another song of the album."""
    from utils.database.song_duplicates import duplicate_key
    return duplicate_key(_REMASTER_RE.sub("", title or ""))


class AlbumTrackCache:
    """Albums found during one run of Fill missing data -> Albums, by their
    tracks: once a song's album is found, the other songs of that artist
    still waiting in the run are looked up in its tracklist first, instead
    of asking every module again for each one.

    A tracklist is loaded (a module's load_tracks(), one more request) only
    while another song of the same artist still waits. Only the artist's own
    tracks are taken - a soundtrack's tracks by other artists are left out -
    and only on an exactly equal title key (_track_key()). Lives only as long
    as the run; nothing is saved."""

    def __init__(self, songs):
        self._waiting: dict = defaultdict(set)
        for song in songs:
            self._waiting[song.artist_id].add(song.id)
        self._tracks: dict = defaultdict(dict)

    def lookup(self, song) -> AlbumMatch | None:
        """The song's album from a tracklist already loaded. Also marks the
        song as no longer waiting."""
        self._waiting[song.artist_id].discard(song.id)
        return self._tracks.get(song.artist_id, {}).get(_track_key(song.title))

    def remember(self, song, match: AlbumMatch) -> None:
        if not match.load_tracks or not self._waiting.get(song.artist_id):
            return
        try:
            tracks = match.load_tracks() or []
        except Exception as e:
            flog(f"Loading the tracklist of '{match.album}' crashed: {e}")
            return
        names = [song.artist.name, *split_synonyms(artist_match_synonyms(song.artist))]
        known = self._tracks[song.artist_id]
        added = 0
        for track in tracks:
            if not isinstance(track, AlbumTrack) or not track.title:
                continue
            if track.artist and not any(_matched_artist_resembles(name, track.artist) for name in names):
                continue
            key = _track_key(track.title)
            if key and key not in known:
                known[key] = AlbumMatch(match.album, _positive_int(track.track_number),
                                        _positive_int(track.disc_number))
                added += 1
        if added:
            print(f"Remembered {added} tracks of {match.album} for the other songs of {song.artist.name}")


def _multi_artist_query(song) -> tuple[list[str] | None, str | None]:
    """(credited artist names, "A x B" main-artist label) for a multi-artist
    song, (None, None) for a single-artist one. Fetchers are still queried with
    the primary artist first - one clean name finds a collab on most services
    more reliably than a joined string - the label is only a fallback."""
    credited = [a.name for a in song_all_artists(song)]
    if len(credited) < 2:
        return None, None
    mains = song_main_artists(song)
    label = MULTI_ARTIST_MAIN_SEPARATOR.join(a.name for a in mains) if len(mains) > 1 else None
    return credited, label


def _latin_search_title(song, title: str) -> str | None:
    """The Latin form of a title in another alphabet, to retry a search with:
    sources may list it only in Latin (N.R.M.'s "Фабрыка" is "Fabryka" on
    MusicBrainz). The owner's songs.title_latin first, else suggest_latin()'s
    guess - only a search query here, never stored."""
    if not has_non_latin(title):
        return None
    stored = getattr(song, "title_latin", None)
    if stored:
        return stored.split("(")[0].strip() or None
    return suggest_latin(title, getattr(song, "language", None), getattr(song.artist, "origin", None))


def discover_album_name(song, modules):
    """The album discover_album() finds, as text."""
    match = discover_album(song, modules)
    return match.album if match else None


def discover_album(song, modules, cache: AlbumTrackCache | None = None) -> AlbumMatch | None:
    """The song's album and its place there: from the run's tracklists
    (`cache`) when one already has it, else from the modules in order, then
    the last-resort fallbacks (those never know a place)."""
    if cache:
        cached = cache.lookup(song)
        if cached:
            print(f"On the tracklist of {cached.album}, loaded for an earlier song")
            return cached
    art_full, son_full, alb = song.artist.name, song.title, song.album
    credited, multi_label = _multi_artist_query(song)
    slog(f"{art_full} - {son_full} ({alb})")
    art_cln_full = art_full.split("(")[0].strip()
    son_cln_full = son_full.split("(")[0].strip()
    art = truncate_at_word(art_full)
    son = truncate_at_word(son_full)
    art_cln = art.split("(")[0].strip()
    son_cln = son.split("(")[0].strip()
    truncated = art_cln != art_cln_full or son_cln != son_cln_full

    # The "_cln" variants above always drop anything in parentheses, on the
    # assumption it's disposable (a "(feat. X)" credit, a "(Radio Edit)"
    # annotation). That assumption doesn't hold for a title/artist where the
    # parenthesised part is the actual name, e.g. "Sad Story (Out of Luck)"
    # or "Merk & Kremont" being truncated by "&" to just "Merk" — both
    # "_cln" attempts above end up querying with essential words missing,
    # and no fetcher can find the song. `raw_differs` catches that: it's
    # True whenever splitting on "(" (or truncate_at_word) actually removed
    # something, and triggers one last retry with the fully original,
    # nothing-stripped artist/title.
    raw_differs = art_cln_full != art_full or son_cln_full != son_full

    synonyms = split_synonyms(artist_match_synonyms(song.artist))
    # A Latin-transliterated title the sources only know in Cyrillic ("V Krayu
    # Magnoliy" -> "В краю магнолий"). A Cyrillic artist synonym ("Ариэль")
    # counts as context, see script_utils.cyrillic_search_variant()
    alt_script_title = cyrillic_search_variant(son_cln, [art_full, alb or "", *synonyms])
    latin_title = _latin_search_title(song, son_cln)

    slog("About to lunch modules' loop")
    for module_id, module_name, module in modules:
        print(f"Looking in {module_name} module")
        match = _call_module_match(module, module_id, module_name, art_cln, son_cln, credited)

        # truncate_at_word() strips trailing "feat. X" / "& X" credits, but it
        # can also cut into a legitimate title/artist (e.g. "Bang, Bang").
        # If the truncated search came up empty, retry with the untruncated
        # strings before giving up on this module.
        if not match and truncated:
            print(f"Looking in {module_name} using untruncated title/artist")
            match = _call_module_match(module, module_id, module_name, art_cln_full, son_cln_full, credited)

        # Both attempts above still strip parentheses from title/artist. If
        # that stripping actually removed something, retry once more with
        # the fully raw strings before giving up on this module.
        if not match and raw_differs:
            print(f"Looking in {module_name} using full title/artist including parentheses")
            match = _call_module_match(module, module_id, module_name, art_full, son_full, credited)

        #Repeat the searching with each of the artist's synonyms
        for synonym in ([] if match else synonyms):
            print(f"Looking for it in {module_name} using synonym {synonym}")
            match = _call_module_match(module, module_id, module_name, synonym, son_cln, credited)
            # The truncated title alone, as in the first attempt, may be a stub
            # ("Bang, Bang" -> "Bang"); retry it whole with the synonym too
            if not match and son_cln != son_cln_full:
                match = _call_module_match(module, module_id, module_name, synonym, son_cln_full, credited)
            if match:
                break

        if not match and multi_label:
            print(f"Looking in {module_name} using all main artists")
            match = _call_module_match(module, module_id, module_name, multi_label, son_cln, credited)

        if not match and alt_script_title:
            print(f"Looking in {module_name} using the title in its own script")
            for alt_artist in [art_cln, *synonyms]:
                match = _call_module_match(module, module_id, module_name, alt_artist, alt_script_title, credited)
                if match:
                    break

        if not match and latin_title:
            print(f"Looking in {module_name} using the title in Latin letters")
            for alt_artist in [art_cln, *synonyms]:
                match = _call_module_match(module, module_id, module_name, alt_artist, latin_title, credited)
                if match:
                    break

        if match:
            if cache:
                cache.remember(song, match)
            return match

    album = album_from_stored_youtube_link(song, credited)
    if album:
        return AlbumMatch(album)

    # Game/anime soundtracks are often stored with the album in the artist
    # field ("Tekken 5 OST") and no fetcher knows the individual track. Only a
    # last resort - a fetcher may still find the proper album name - and not
    # recorded as any fetcher's success in the Statistics menu.
    if is_soundtrack_artist(art_full):
        print(f"No fetcher found an album; artist looks like a soundtrack, using it as the album: {art_full}")
        return AlbumMatch(art_full)

    # A cover/unplugged version no fetcher knows is almost always a standalone
    # upload, not part of an album. Checked after the soundtrack fallback,
    # which names a more specific album. Same last-resort / no-stats rules as above.
    if has_singles_marker(art_full, son_full):
        print(f"No fetcher found an album; song looks like a cover/unplugged version, filing it under {SINGLES_ALBUM}")
        return AlbumMatch(SINGLES_ALBUM)

    slog("Gave up =)")
    return None


def album_from_stored_youtube_link(song, credited_artists: list[str] | None = None) -> str | None:
    """Album read off the song's own YouTube link (Song.youtube_video_id) when
    no fetcher found one - an official "Provided to YouTube" upload names it
    even for a release no service's search knows ("Злипні - Глюки" -> "Ґрунт").
    Last resort, since it costs a yt-dlp call; validated like a fetcher result,
    with the artist's synonyms as alternative names (the upload may credit
    "Zlypni"). Not counted in the Statistics menu - it isn't a module."""
    from utils.youtube.manage_youtube_playlists import get_video_album

    found = get_video_album(getattr(song, "youtube_video_id", None))
    if not found:
        return None
    album, track, artist = found
    print(f"Looking for the album in the song's YouTube link [{song.youtube_video_id}]")
    result = DiscoveryResult(album=album, matched_title=track or None, matched_artist=artist or None)
    for name in [song.artist.name, *split_synonyms(artist_match_synonyms(song.artist))]:
        validated = _validate_result(result, name, song.title, "YouTube link", credited_artists)
        if validated:
            return validated
    return None


def _validate_year_result(result, queried_artist: str, queried_query: str, module_name: str,
                          credited_artists: list[str] | None = None) -> YearDiscoveryResult | None:
    """Same defensive sanity-checking as _validate_result(), adapted for a
    release year: rejects crashy/wrong-typed/implausible years, and
    independently re-checks any reported matched_title/matched_artist
    against what was actually searched for before trusting the year.
    A bare int is wrapped, so callers always get a YearDiscoveryResult."""
    if result is None:
        return None

    if isinstance(result, int) and not isinstance(result, bool):
        result = YearDiscoveryResult(year=result)
    if isinstance(result, YearDiscoveryResult):
        year, matched_title, matched_artist = result.year, result.matched_title, result.matched_artist
    else:
        flog(f"[{module_name}] returned unexpected type {type(result).__name__} for a release year, discarding")
        return None

    current_year = datetime.now().year
    if not isinstance(year, int) or not (MIN_PLAUSIBLE_RELEASE_YEAR <= year <= current_year + 1):
        flog(f"[{module_name}] returned implausible release year '{year}', discarding")
        return None

    if matched_title:
        threshold = scaled_similarity_threshold(queried_query, matched_title, SPELLING_CHECK_THRESHOLD)
        title_sim = similarity(queried_query, matched_title)
        if not title_resembles(queried_query, matched_title):
            flog(f"[{module_name}] matched title '{matched_title}' doesn't resemble query "
                 f"'{queried_query}' (sim={title_sim:.2f} < {threshold:.2f}), discarding")
            return None

    if matched_artist and not _matched_artist_resembles(queried_artist, matched_artist, credited_artists):
        flog(f"[{module_name}] matched artist '{matched_artist}' doesn't resemble query "
             f"'{queried_artist}' (sim={similarity(queried_artist, matched_artist):.2f}), discarding")
        return None

    return result


def _call_year_module(module, module_id: str, module_name: str, artist: str, query: str, is_single: bool,
                      credited_artists: list[str] | None = None, songs=None) -> YearDiscoveryResult | None:
    """Run a module's get_release_year(), isolated from crashes and bad output.
    Mirrors _call_module(), but counted in the separate year stats file.

    A module whose get_release_year() takes a `songs` keyword also gets the
    Song rows being looked up (the single, or the album's songs), e.g. to
    reuse a stored youtube_video_id. Everything else keeps the plain
    (artist, query, is_single) contract."""
    record_year_invocation(module_id)
    try:
        if songs and "songs" in inspect.signature(module.get_release_year).parameters:
            result = module.get_release_year(artist, query, is_single, songs=songs)
        else:
            result = module.get_release_year(artist, query, is_single)
    except Exception as e:
        flog(f"[{module_name}] crashed while looking up release year for '{artist} - {query}': {e}")
        print(f"{module_name} failed unexpectedly, skipping it for this lookup")
        return None
    if result is None:
        flog(f"[{module_name}] found no release year for '{artist} - {query}' (is_single={is_single})")
    result = _validate_year_result(result, artist, query, module_name, credited_artists)
    if result:
        record_year_success(module_id)
    return result


def discover_release_year(artist: str, query: str, is_single: bool, modules, artist_synonyms: str | None = None,
                          song=None) -> int | None:
    """discover_release_year_result(), reduced to just the year."""
    result = discover_release_year_result(artist, query, is_single, modules, artist_synonyms, song)
    return result.year if result else None


def discover_release_year_result(artist: str, query: str, is_single: bool, modules,
                                 artist_synonyms: str | None = None, song=None,
                                 alt_script_query: str | None = None,
                                 album_songs=None) -> YearDiscoveryResult | None:
    """Look up a release year for either an album (query = album title,
    is_single=False) or a standalone single (query = song title,
    is_single=True), trying each enabled year-capable module in order until
    one returns a validated year.

    Mirrors discover_album_name()'s truncation/fallback stages, but operates
    directly on an (artist, query) pair rather than a Song object, since the
    caller may be querying once on behalf of several songs that share an
    album.

    `song` is passed only for a single (is_single=True), so a multi-artist
    single gets the same credited-artist validation and "A x B" fallback
    query as discover_album_name(). An album lookup is keyed on the primary
    artist alone - a guest on one track isn't the album's artist.
    `album_songs` (an album lookup) are only handed on to modules that read
    data stored on the songs (see _call_year_module()).
    """
    credited, multi_label = _multi_artist_query(song) if song is not None else (None, None)
    songs = [song] if song is not None else album_songs
    synonyms = split_synonyms(artist_synonyms)
    art_full, qry_full = artist, query
    art_cln_full = art_full.split("(")[0].strip()
    qry_cln_full = qry_full.split("(")[0].strip()
    art = truncate_at_word(art_full)
    qry = truncate_at_word(qry_full)
    art_cln = art.split("(")[0].strip()
    qry_cln = qry.split("(")[0].strip()
    truncated = art_cln != art_cln_full or qry_cln != qry_cln_full
    raw_differs = art_cln_full != art_full or qry_cln_full != qry_full

    for module_id, module_name, module in modules:
        print(f"Looking for release year in {module_name} module")
        result = _call_year_module(module, module_id, module_name, art_cln, qry_cln, is_single, credited, songs)

        if not result and truncated:
            print(f"Looking for release year in {module_name} using untruncated artist/query")
            result = _call_year_module(module, module_id, module_name, art_cln_full, qry_cln_full, is_single, credited, songs)

        if not result and raw_differs:
            print(f"Looking for release year in {module_name} using full artist/query including parentheses")
            result = _call_year_module(module, module_id, module_name, art_full, qry_full, is_single, credited, songs)

        # Each synonym on its own - the column is a comma-separated list, and
        # sending it whole ("Taras Chubai, Тарас Чубай") matches nothing
        for synonym in ([] if result else synonyms):
            print(f"Looking for release year in {module_name} using synonym {synonym}")
            result = _call_year_module(module, module_id, module_name, synonym, qry_cln, is_single, credited, songs)
            # The truncated query is only a stub for a title with a comma
            # ("Там, де ти є" -> "Там"); retry it whole with the synonym too
            if not result and qry_cln != qry_cln_full:
                result = _call_year_module(module, module_id, module_name, synonym, qry_cln_full, is_single,
                                           credited, songs)
            if result:
                break

        if not result and multi_label:
            print(f"Looking for release year in {module_name} using all main artists")
            result = _call_year_module(module, module_id, module_name, multi_label, qry_cln, is_single, credited, songs)

        # A Latin-transliterated query the sources only know in its own script
        # ("Neformat" -> "Неформат"), see script_utils.cyrillic_search_variant()
        if not result and alt_script_query:
            print(f"Looking for release year in {module_name} using the query in its own script")
            for alt_artist in [art_cln, *synonyms]:
                result = _call_year_module(module, module_id, module_name, alt_artist, alt_script_query, is_single,
                                           credited, songs)
                if result:
                    break

        if result:
            return result
    slog("Gave up looking for a release year =)")
    return None


def release_year_from_stored_link(songs) -> YearDiscoveryResult | None:
    """Year read off a Spotify page the user stored on one of these songs
    (Song.spotify_url) - for a release no fetcher's search finds. Tried
    before any module and trusted as is (the user picked the page), apart
    from the plausible-range check."""
    # Imported here: the fetchers are otherwise only ever loaded by path
    # (see _import_module), and selenium shouldn't load just for this module.
    from utils.discoveries.discovery_modules import spotify_fetcher

    for song in songs:
        if not song.spotify_url:
            continue
        result = spotify_fetcher.get_release_year_from_url(song.spotify_url)
        if result and MIN_PLAUSIBLE_RELEASE_YEAR <= result.year <= datetime.now().year + 1:
            return result
        flog(f"[Spotify link] no release year on {song.spotify_url} stored for '{song.artist.name} - {song.title}'")
    return None
