from utils.common.soundtrack_names import soundtrack_album, soundtrack_work
from utils.common.text_utils import (
    truncate_at_word,
    is_blacklisted_album,
    is_soundtrack_artist,
    has_singles_marker,
    similarity,
    scaled_similarity_threshold,
    title_resembles,
    resembles_across_scripts,
    remove_brackets,
)
from utils.discoveries.discovery_result import (AlbumTrack, DiscoveryResult, GenreDiscoveryResult,
                                                LanguageDiscoveryResult, LyricsDiscoveryResult, YearDiscoveryResult)
from utils.discoveries.discovery_settings import (ALBUMS, GENRES, KINDS, LANGUAGES, LYRICS, YEARS,
                                                  reconcile_discovery_config)
from utils.discoveries.discovery_stats import record_invocation, record_success
from config.constants import (
    ALBUM_EXTERNAL_ID_COLUMNS,
    DISCOVERY_ALBUM_TYPES,
    GENRE_LEVEL_ARTIST,
    GENRE_MAX_PER_SONG,
    GENRE_MAX_QUERIES,
    LANGUAGE_DETECT_SURE,
    LANGUAGE_MAX_QUERIES,
    LYRICS_DURATION_TOLERANCE_S,
    MIN_PLAUSIBLE_RELEASE_YEAR,
    MUSICBRAINZ_REISSUE_AFTER_YEARS,
    SINGLES_ALBUM,
    SPELLING_CHECK_THRESHOLD,
)
from datetime import datetime
import ast
import inspect
import importlib.util
import sys
from pathlib import Path
from utils.common.debug import flog, mlog, slog
from utils.common.normalizer import composed, name_key, normalize
from utils.database.song_artists import song_all_artists, song_main_artists
from utils.database.latin_names import artist_match_synonyms, artist_synonyms, song_titles
from utils.database.other_titles import other_titles
from utils.database.covers import is_translated
from utils.database.genres import canonical_genre, clean_genres, is_known_genre
from utils.common.artist_credits import propose_artist_split, split_synonyms
from utils.common.script_utils import cyrillic_search_variant, has_non_latin
from utils.common.romanize import suggest_latin
from utils.common.version_names import read_version
from utils.common.language_names import written_otherwise
from config.constants import MULTI_ARTIST_MAIN_SEPARATOR
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Callable
import re

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
    which modules take part in each kind of lookup (discovery_settings.KINDS)."""
    try:
        tree = ast.parse(script_path.read_text(encoding="utf-8"), filename=str(script_path))
    except (OSError, SyntaxError):
        return False

    return any(
        isinstance(node, ast.FunctionDef) and node.name == func_name
        for node in tree.body
    )


def _module_files(kind: str) -> dict:
    """Module files (by id) that statically define the kind's function."""
    function = KINDS[kind].function
    return {p.stem: p for p in sorted(_discovery_modules_dir().glob("*.py"))
            if _module_defines_function(p, function)}


def _enabled_modules(module_files: dict, config: dict) -> list:
    """[(module_id, display name, module), ...] of the enabled modules, imported, in
    the configured order."""
    modules = []
    for module_id in config["order"]:
        if config["enabled"].get(module_id, True):
            module = _import_module(module_files[module_id])
            modules.append((module_id, getattr(module, "MODULE_NAME", module_id), module))
    return modules


def _modules_metadata(module_files: dict, config: dict) -> list:
    """[(module_id, display name), ...] of every module, enabled or not, in the
    configured order, for the Settings menu (enable/disable + reorder screens).

    Display names are read by statically parsing each file's ``MODULE_NAME``
    (see _read_module_name) rather than importing the modules, so opening the
    Settings menu doesn't run every module's import-time side effects."""
    return [(module_id, _read_module_name(module_files[module_id])) for module_id in config["order"]]


def load_discovery_modules(kind: str = ALBUMS):
    """The *enabled* modules of a kind of lookup, in the user's configured
    order (see discovery_settings.py), ready to be passed to
    discover_album_name() / discover_release_year_result() / discover_lyrics()."""
    module_files = _module_files(kind)
    return _enabled_modules(module_files, reconcile_discovery_config(module_files.keys(), kind))


def enabled_module_ids(kind: str) -> set:
    """Ids of the enabled modules of a kind, without importing any."""
    config = reconcile_discovery_config(_module_files(kind).keys(), kind)
    return {module_id for module_id in config["order"] if config["enabled"].get(module_id, True)}


def load_all_discovery_modules_metadata(kind: str = ALBUMS):
    module_files = _module_files(kind)
    return _modules_metadata(module_files, reconcile_discovery_config(module_files.keys(), kind))


def _matched_artist_resembles(queried_artist: str, matched_artist: str, credited_artists: list[str] | None = None) -> bool:
    """Does the artist a module says it matched resemble what we searched for?

    `credited_artists` is only passed for a multi-artist song (all its
    artists, see song_artists.py). We query with the primary artist alone, but
    services often credit the whole collab in one field ("Sw@da, Maxim &
    Niczos") - plain similarity to "Sw@da" then fails, so also accept a
    matched field that contains any credited artist as whole words. Or just
    one of them, spelt their way: Spotify's first artist of "Alizbar - Idje
    Sen" is "Ann' Sannat", stored as "Ann`Sannat", so each credited artist
    gets the same checks as the queried one."""
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
            (normalize(name) and f" {normalize(name)} " in matched_padded)
            or _matched_artist_resembles(name, matched_artist)
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
        album = result.album
    elif isinstance(result, str):
        album = result
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
    return album if _match_resembles(result, queried_artist, queried_title, module_name, credited_artists) else None


def _match_resembles(result, queried_artist: str, queried_title: str, module_name: str,
                     credited_artists: list[str] | None) -> bool:
    """Whether the title and artist a module says it matched (when it says)
    resemble what was searched for; what doesn't goes into debug.log."""
    matched_title = getattr(result, "matched_title", None)
    matched_artist = getattr(result, "matched_artist", None)
    if matched_title and not title_resembles(queried_title, matched_title):
        threshold = scaled_similarity_threshold(queried_title, matched_title, SPELLING_CHECK_THRESHOLD)
        flog(f"[{module_name}] matched title '{matched_title}' doesn't resemble query "
             f"'{queried_title}' (sim={similarity(queried_title, matched_title):.2f} < {threshold:.2f}), discarding")
        return False
    if matched_artist and not _matched_artist_resembles(queried_artist, matched_artist, credited_artists):
        flog(f"[{module_name}] matched artist '{matched_artist}' doesn't resemble query "
             f"'{queried_artist}' (sim={similarity(queried_artist, matched_artist):.2f}), discarding")
        return False
    return True


@dataclass
class AlbumMatch:
    """An album found for a song, with the song's place on it when the
    module knew it (None = unknown; disc None also means a one-disc album).
    load_tracks, needs_review, year, album_type, album_ids, matched_title,
    release_kind: the module's, checked, see DiscoveryResult (matched_title:
    the recording or track it was found by). source: the module's id (None
    for a fallback)."""
    album: str
    track_number: int | None = None
    disc_number: int | None = None
    load_tracks: Callable[[], list[AlbumTrack]] | None = None
    needs_review: bool = False
    year: int | None = None
    album_type: str | None = None
    album_ids: dict = field(default_factory=dict)
    source: str | None = None
    matched_title: str | None = None
    release_kind: str | None = None


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
    artist, title = composed(artist), composed(title)
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
    year = result.year
    if not (isinstance(year, int) and MIN_PLAUSIBLE_RELEASE_YEAR <= year <= datetime.now().year + 1):
        if year is not None:
            flog(f"[{module_name}] returned implausible release year '{year}' with the album, discarding it")
        year = None
    ids = result.album_ids if isinstance(result.album_ids, dict) else {}
    return AlbumMatch(album, _positive_int(result.track_number), _positive_int(result.disc_number),
                      result.load_tracks if callable(result.load_tracks) else None,
                      result.needs_review is True, year,
                      result.album_type if result.album_type in DISCOVERY_ALBUM_TYPES else None,
                      {column: value for column, value in ids.items()
                       if column in ALBUM_EXTERNAL_ID_COLUMNS and _is_external_id(value)},
                      module_id,
                      result.matched_title if isinstance(result.matched_title, str) else None,
                      result.release_kind if isinstance(result.release_kind, str) else None)


def _is_external_id(value) -> bool:
    return (isinstance(value, str) and bool(value.strip())) or _positive_int(value) is not None


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
                                        _positive_int(track.disc_number), year=match.year,
                                        album_type=match.album_type, album_ids=match.album_ids,
                                        source=match.source, matched_title=track.title,
                                        release_kind=match.release_kind)
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
    unsure = None  # the first needs_review find, used only if nothing better turns up

    for module_id, module_name, module in modules:
        print(f"Looking in {module_name} module")
        match = _call_module_match(module, module_id, module_name, art_cln, son_cln, credited)

        # truncate_at_word() strips trailing "feat. X" / "& X" credits, but it
        # can also cut into a legitimate title/artist (e.g. "Bang, Bang").
        # If the truncated search came up empty, retry with the untruncated
        # strings before giving up on this module.
        if not match and truncated:
            mlog(f"Looking in {module_name} using untruncated title/artist")
            match = _call_module_match(module, module_id, module_name, art_cln_full, son_cln_full, credited)

        # Both attempts above still strip parentheses from title/artist. If
        # that stripping actually removed something, retry once more with
        # the fully raw strings before giving up on this module.
        if not match and raw_differs:
            mlog(f"Looking in {module_name} using full title/artist including parentheses")
            match = _call_module_match(module, module_id, module_name, art_full, son_full, credited)

        #Repeat the searching with each of the artist's synonyms
        for synonym in ([] if match else synonyms):
            mlog(f"Looking for it in {module_name} using synonym {synonym}")
            match = _call_module_match(module, module_id, module_name, synonym, son_cln, credited)
            # The truncated title alone, as in the first attempt, may be a stub
            # ("Bang, Bang" -> "Bang"); retry it whole with the synonym too
            if not match and son_cln != son_cln_full:
                match = _call_module_match(module, module_id, module_name, synonym, son_cln_full, credited)
            if match:
                break

        if not match and multi_label:
            mlog(f"Looking in {module_name} using all main artists")
            match = _call_module_match(module, module_id, module_name, multi_label, son_cln, credited)

        if not match and alt_script_title:
            mlog(f"Looking in {module_name} using the title in its own script")
            for alt_artist in [art_cln, *synonyms]:
                match = _call_module_match(module, module_id, module_name, alt_artist, alt_script_title, credited)
                if match:
                    break

        if not match and latin_title:
            mlog(f"Looking in {module_name} using the title in Latin letters")
            for alt_artist in [art_cln, *synonyms]:
                match = _call_module_match(module, module_id, module_name, alt_artist, latin_title, credited)
                if match:
                    break

        if match and match.needs_review:
            unsure = unsure or match
        elif match:
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
        work = soundtrack_work(art_full)
        album = soundtrack_album(work) if work else art_full
        if not is_blacklisted_album(album):
            print(f"No fetcher found an album; artist looks like a soundtrack, using it as the album: {album}")
            return AlbumMatch(album)

    # A cover/unplugged version no fetcher knows is almost always a standalone
    # upload, not part of an album. Checked after the soundtrack fallback,
    # which names a more specific album. Same last-resort / no-stats rules as above.
    if has_singles_marker(art_full, son_full):
        print(f"No fetcher found an album; song looks like a cover/unplugged version, filing it under {SINGLES_ALBUM}")
        return AlbumMatch(SINGLES_ALBUM)

    if unsure:
        return unsure

    flog(f"No module found an album for '{art_full} - {son_full}'")
    return None


def discover_album_after_swap(song, modules) -> AlbumMatch | None:
    """The album the modules know for the song read the other way round: its
    title as the artist and its artist as the title ("Dobro Te Čuvaju" - "Džoni
    Midnajt Trio"). Only for a song discover_album() found nothing for; finding
    one is a guess the owner confirms, see utils/ui/album_swap_review.py. No
    fallbacks here: a YouTube link or a "cover" in a name says nothing about
    which way round the names are."""
    def clean(name: str) -> str:
        return name.split("(")[0].strip()

    # As in discover_album(): the truncated names first, then whole ones
    queries = dict.fromkeys([(clean(truncate_at_word(song.title)), clean(truncate_at_word(song.artist.name))),
                             (clean(song.title), clean(song.artist.name))])
    for module_id, module_name, module in modules:
        print(f"Looking in {module_name} module with the artist and title swapped")
        for artist, title in queries:
            match = _call_module_match(module, module_id, module_name, artist, title)
            if match:
                return match
    flog(f"No module found an album for '{song.artist.name} - {song.title}' with the names swapped either")
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
        year = result.year
    else:
        flog(f"[{module_name}] returned unexpected type {type(result).__name__} for a release year, discarding")
        return None

    current_year = datetime.now().year
    if not isinstance(year, int) or not (MIN_PLAUSIBLE_RELEASE_YEAR <= year <= current_year + 1):
        flog(f"[{module_name}] returned implausible release year '{year}', discarding")
        return None

    return result if _match_resembles(result, queried_artist, queried_query, module_name, credited_artists) else None


def year_after_the_song(year: int | None, artist: str, title: str, modules, source: str | None = None) -> bool:
    """Is `year` a reissue's or a compilation's - more than
    MUSICBRAINZ_REISSUE_AFTER_YEARS after the song first came out on any
    release MusicBrainz knows? Spotify has Anawa's 1973 album with Andrzej
    Zaucha only as a 2014 reissue, while MusicBrainz has the song on a 2004
    compilation already. Asked of a module among `modules` that can tell
    (first_appearance_year(): one request), not when it is `source` itself,
    whose answer already left reissues out. False when nothing can tell."""
    if not year or not title:
        return False
    for module_id, module_name, module in modules:
        first_appearance = getattr(module, "first_appearance_year", None)
        if first_appearance is None:
            continue
        if module_id == source:
            return False
        try:
            first = first_appearance(composed(artist), composed(title))
        except Exception as e:
            flog(f"[{module_name}] crashed while looking up the first release of '{artist} - {title}': {e}")
            return False
        if first and year > first + MUSICBRAINZ_REISSUE_AFTER_YEARS:
            flog(f"[{module_name}] '{artist} - {title}' was out by {first}, so {year} is a later release's year, discarding")
            return True
        return False
    return False


def _call_year_module(module, module_id: str, module_name: str, artist: str, query: str, is_single: bool,
                      credited_artists: list[str] | None = None, songs=None) -> YearDiscoveryResult | None:
    """Run a module's get_release_year(), isolated from crashes and bad output.
    Mirrors _call_module(), but counted in the separate year stats file.

    A module whose get_release_year() takes a `songs` keyword also gets the
    Song rows being looked up (the single, or the album's songs), e.g. to
    reuse a stored youtube_video_id. Everything else keeps the plain
    (artist, query, is_single) contract."""
    artist, query = composed(artist), composed(query)
    record_invocation(module_id, YEARS)
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
        record_success(module_id, YEARS)
    return result


def discover_release_year_result(artist: str, query: str, is_single: bool, modules,
                                 artist_synonyms: str | None = None, song=None,
                                 alt_script_query: str | None = None,
                                 album_songs=None, held: bool = True) -> YearDiscoveryResult | None:
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
    data stored on the songs (see _call_year_module()). A year later than
    the song (an album's first song) first came out is no year
    (year_after_the_song()): the next module is asked - unless not `held`:
    a project's recording is years after its studio song by nature.
    """
    credited, multi_label = _multi_artist_query(song) if song is not None else (None, None)
    songs = [song] if song is not None else album_songs
    # The song a year is held against (year_after_the_song()): an album's first
    song_title = None if not held else songs[0].title if songs else (query if is_single else None)
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
            mlog(f"Looking for release year in {module_name} using untruncated artist/query")
            result = _call_year_module(module, module_id, module_name, art_cln_full, qry_cln_full, is_single, credited, songs)

        if not result and raw_differs:
            mlog(f"Looking for release year in {module_name} using full artist/query including parentheses")
            result = _call_year_module(module, module_id, module_name, art_full, qry_full, is_single, credited, songs)

        # Each synonym on its own - the column is a comma-separated list, and
        # sending it whole ("Taras Chubai, Тарас Чубай") matches nothing
        for synonym in ([] if result else synonyms):
            mlog(f"Looking for release year in {module_name} using synonym {synonym}")
            result = _call_year_module(module, module_id, module_name, synonym, qry_cln, is_single, credited, songs)
            # The truncated query is only a stub for a title with a comma
            # ("Там, де ти є" -> "Там"); retry it whole with the synonym too
            if not result and qry_cln != qry_cln_full:
                result = _call_year_module(module, module_id, module_name, synonym, qry_cln_full, is_single,
                                           credited, songs)
            if result:
                break

        if not result and multi_label:
            mlog(f"Looking for release year in {module_name} using all main artists")
            result = _call_year_module(module, module_id, module_name, multi_label, qry_cln, is_single, credited, songs)

        # A Latin-transliterated query the sources only know in its own script
        # ("Neformat" -> "Неформат"), see script_utils.cyrillic_search_variant()
        if not result and alt_script_query:
            mlog(f"Looking for release year in {module_name} using the query in its own script")
            for alt_artist in [art_cln, *synonyms]:
                result = _call_year_module(module, module_id, module_name, alt_artist, alt_script_query, is_single,
                                           credited, songs)
                if result:
                    break

        if result and year_after_the_song(result.year, art_cln, song_title, modules,
                                          source=module_id if is_single else None):
            result = None
        if result:
            return result
    flog(f"No module found a release year for '{artist} - {query}'")
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


@dataclass
class LyricsMatch:
    """Lyrics a module found for a song. sure: the same title (one of the
    song's titles) and a resembling artist, or read from the song's own file;
    a title that only resembles one is the owner's to confirm. synced_fits:
    the found recording's length is within LYRICS_DURATION_TOLERANCE_S of
    ours (or one of them is unknown), so its synced lines fit our recording -
    otherwise only the plain text is worth keeping (a music video's intro, a
    radio edit). duration_s: ours and the found one's, for showing them.
    other_script: the text is mostly in a script the song's stored language
    isn't written in (language_names.written_otherwise()) - never sure."""
    result: LyricsDiscoveryResult
    module_id: str
    sure: bool
    duration_s: float | None = None
    found_duration_s: float | None = None
    synced_fits: bool = True
    other_script: bool = False


@dataclass
class LyricsSearch:
    """What discover_lyrics() found: the best match (None = nothing), the
    modules that answered without a fitting text (`missed`, worth
    remembering) and those that couldn't be asked (`failed`: no network,
    rate limited - asked again next time)."""
    match: LyricsMatch | None = None
    missed: list = field(default_factory=list)
    failed: list = field(default_factory=list)


def _has_text(result) -> bool:
    return isinstance(result, LyricsDiscoveryResult) and bool(
        result.instrumental or (result.plain or "").strip() or (result.synced or "").strip())


# What isn't words: an address (a download site's tag in an mp3's lyrics frame,
# "www.t.me/pmedia_music", "ElectroHouse.ucoz.com" - 10 songs had one as lyrics)
_ADDRESS_RE = re.compile(r"https?://\S+|www\.\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|info|biz|io|it|pl|ru|cz|me)\b\S*",
                         re.IGNORECASE)
_INSTRUMENTAL_NOTE_RE = re.compile(r"^\W*instrumental\W*$", re.IGNORECASE)


def _read_as_meant(result: LyricsDiscoveryResult) -> LyricsDiscoveryResult:
    """A text that is only "Instrumental" is a claim of none (LRCLIB's "Linkin
    Park - Session"); one of only addresses, signs and times (LRCLIB's blank
    lines for "Acid Rain") is no text at all."""
    plain = result.plain or ""
    if _INSTRUMENTAL_NOTE_RE.match(plain):
        return replace(result, plain=None, synced=None, instrumental=True)
    if (plain or result.synced) and not re.search(r"[^\W\d_]", _ADDRESS_RE.sub(" ", plain + (result.synced or ""))):
        return replace(result, plain=None, synced=None)
    return result


def _lyrics_match(result: LyricsDiscoveryResult, song, queried_artist: str, module_id: str, module_name: str,
                  duration_s: float | None, credited: list[str] | None) -> LyricsMatch | None:
    """The candidate checked against the song, or None when it's another song."""
    if isinstance(result, LyricsDiscoveryResult):
        result = _read_as_meant(result)
    if not _has_text(result):
        return None
    if result.from_song_file:
        return LyricsMatch(result, module_id, True, duration_s, result.duration_s)
    if not (result.matched_title and result.matched_artist):
        flog(f"[{module_name}] lyrics without the title/artist they belong to, discarding")
        return None
    # A translated cover isn't the original's words (covers.py)
    covered_title = None if is_translated(song) else getattr(song, "covered_title", None)
    titles = [*song_titles(song), *([covered_title] if covered_title else [])]
    # A bracket on either side is a credit or a note, not other words: "ARMANI TOP
    # (prod.bovskey)" is LRCLIB's and Genius's "Armani Top" (a version's marker
    # isn't in its title any more - songs.version_kind)
    keys = {key for t in titles for key in (normalize(t), normalize(remove_brackets(t))) if key}
    # ... and so is a dashed one: Genius's "Nazywali go Marynarz – szanta narciarska" -
    # unless it names a version ours isn't: "最果て (Instrumental)" isn't "最果て"
    found_keys = {normalize(result.matched_title)}
    found_version = read_version(result.matched_title)
    if found_version is None or found_version.kind == getattr(song, "version_kind", None):
        found_keys |= {normalize(remove_brackets(result.matched_title)),
                       normalize(_DASHED_NOTE.sub("", result.matched_title))}
    exact = bool(found_keys & keys)
    # One song of a medley: its words are only part of ours - the owner decides
    parts = [part for t in titles for part in _medley_parts(t)]
    if not exact and not any(title_resembles(t, result.matched_title) for t in [*titles, *parts]):
        slog(f"[{module_name}] lyrics of '{result.matched_title}' aren't '{song.title}'")
        return None
    if not _matched_artist_resembles(queried_artist, result.matched_artist, credited):
        slog(f"[{module_name}] lyrics by '{result.matched_artist}' aren't by '{queried_artist}'")
        return None
    fits = not (duration_s and result.duration_s) or abs(duration_s - result.duration_s) <= LYRICS_DURATION_TOLERANCE_S
    # Another song's words under this one's names: LRCLIB's "Fisz Emade - Serce" is Jandro's Russian
    other_script = written_otherwise(result.plain or result.synced, getattr(song, "language", None))
    if other_script:
        slog(f"[{module_name}] lyrics of '{result.matched_title}' are in a script {song.language} isn't written in")
    return LyricsMatch(result, module_id, exact and not other_script, duration_s, result.duration_s, fits, other_script)


def _match_rank(match: LyricsMatch) -> tuple:
    """Higher is better: sure, then words over a claim of none (LRCLIB holds "Jump Up" both
    with its text and as "instrumental"), then synced lines that fit, then the closest length."""
    difference = (abs(match.duration_s - match.found_duration_s)
                  if match.duration_s and match.found_duration_s else float("inf"))
    return match.sure, not match.result.instrumental, match.synced_fits and bool(match.result.synced), -difference


def _call_lyrics_module(module, module_id: str, module_name: str, artist: str, title: str,
                        duration_s: float | None, song) -> list | None:
    """Run a module's get_lyrics(), isolated from crashes; its candidates as a
    list, None when the module failed (it raised: no network, rate limited)."""
    artist, title = composed(artist), composed(title)
    record_invocation(module_id, LYRICS)
    try:
        result = module.get_lyrics(artist, title, duration_s, songs=[song])
    except Exception as e:
        flog(f"[{module_name}] failed while looking up lyrics for '{artist} - {title}': {e}")
        print(f"{module_name} failed, it's asked again next time")
        return None
    candidates = [] if result is None else result if isinstance(result, list) else [result]
    if not candidates:
        flog(f"[{module_name}] found no lyrics for '{artist} - {title}'")
    return candidates


def _lyrics_queries(song) -> list[tuple[str, str]]:
    """(artist, title) to ask a lyrics module, in order, until one fits: the
    artist's name, synonyms, the parts of a joined name ("Alice Cooper & Bee
    Gees"), the song's main artists and all of them joined by "&", each with the title, the title without
    its brackets ("(prod. X)") and the other titles ("W moim ogródecku" for
    "Studzianki") and its Latin form (LyricsTranslate's addresses are in Latin);
    then for a cover, the covered artist with the original's
    title - a cover sings its words ("Where Is My Mind" by Pixies), unless
    it's a translated one (songs.cover_translated: Mistmorn's "Дарма" isn't
    American Football's English "Never Meant")."""
    proposal = propose_artist_split(song.artist.name)
    mains = [artist.name for artist in song_main_artists(song)]
    # A duet as the sites write it: "Anna Maria Jopek & Branford Marsalis"
    names = [song.artist.name, *artist_synonyms(song.artist), *(proposal.main if proposal else []), *mains,
             *([" & ".join(mains)] if len(mains) > 1 else [])]
    titles = [song.title, remove_brackets(song.title), getattr(song, "title_latin", None), *other_titles(song)]
    queries = [(artist, title) for title in dict.fromkeys(titles) if title for artist in dict.fromkeys(names)]
    covered = getattr(song, "covered_artist", None)
    if covered is not None and not is_translated(song):
        queries.append((covered.name, getattr(song, "covered_title", None) or song.title))
    # A medley's songs one by one, last: "Steel for Humans / Lazare"
    queries += [(artist, part) for artist, title in list(queries) for part in _medley_parts(title)]
    return list(dict.fromkeys(queries))


_DASHED_NOTE = re.compile(r"\s+[-–—]\s+[^-–—]+$")


def _medley_parts(title: str) -> list[str]:
    """"Steel for Humans / Lazare" -> both songs; [] for a title of one."""
    parts = [part.strip() for part in title.split(" / ")]
    return parts if len(parts) > 1 and all(parts) else []


def discover_lyrics(song, modules, duration_s: float | None = None) -> LyricsSearch:
    """The best lyrics the given get_lyrics() modules find for `song`, in
    their order: the first sure match with words wins; one that isn't sure, or
    a source's "instrumental", is kept while the next modules may still find a
    sure text (LRCLIB's instrumental entry for John Legend's "Green Light" hid
    Genius's words). Each module is asked _lyrics_queries() in order until it
    has a candidate that fits. `duration_s` is our length of the song (None =
    unknown)."""
    credited, _ = _multi_artist_query(song)
    queries = _lyrics_queries(song)
    search = LyricsSearch()
    for module_id, module_name, module in modules:
        print(f"Looking for lyrics in {module_name} module")
        found = None
        for artist, title in queries:
            candidates = _call_lyrics_module(module, module_id, module_name, artist, title, duration_s, song)
            if candidates is None:
                break
            matches = [m for m in (_lyrics_match(c, song, artist, module_id, module_name, duration_s, credited)
                                   for c in candidates) if m]
            if matches:
                found = max(matches, key=_match_rank)
                break
        if candidates is None and found is None:
            search.failed.append(module_id)
            continue
        if found is None:
            search.missed.append(module_id)
            continue
        record_success(module_id, LYRICS)
        if search.match is None or _match_rank(found) > _match_rank(search.match):
            search.match = found
        if search.match.sure and not search.match.result.instrumental:
            return search
    if search.match is None:
        flog(f"No module found lyrics for '{song.artist.name} - {song.title}'")
    return search


@dataclass
class LanguageMatch:
    """A language a module found for a song. sure: the source says it
    outright, or reads it from the lyrics with LANGUAGE_DETECT_SURE - never a
    never_sure source's."""
    result: LanguageDiscoveryResult
    module_id: str
    sure: bool


@dataclass
class LanguageSearch:
    """What discover_language() found: the best match (None = nothing), the
    modules that answered without one and those that couldn't be asked."""
    match: LanguageMatch | None = None
    missed: list = field(default_factory=list)
    failed: list = field(default_factory=list)


def _language_match(result, song, queried_artist: str, module_id: str, module_name: str,
                    credited: list[str] | None) -> LanguageMatch | None:
    """The answer checked against the song, or None when it's another song's."""
    if not isinstance(result, LanguageDiscoveryResult) or not result.language:
        return None
    sure = ((result.confidence is None or result.confidence >= LANGUAGE_DETECT_SURE)
            and not (result.needs_origin or result.never_sure))
    if result.from_song:
        return LanguageMatch(result, module_id, sure)
    if not (result.matched_title and result.matched_artist):
        flog(f"[{module_name}] a language without the title/artist it belongs to, discarding")
        return None
    if not any(title_resembles(title, result.matched_title) for title in song_titles(song)):
        slog(f"[{module_name}] the language of '{result.matched_title}' isn't '{song.title}'s")
        return None
    if not _matched_artist_resembles(queried_artist, result.matched_artist, credited):
        slog(f"[{module_name}] the language of a song by '{result.matched_artist}' isn't '{queried_artist}'s")
        return None
    return LanguageMatch(result, module_id, sure)


def discover_language(song, modules) -> LanguageSearch:
    """The language the given get_language() modules find for `song`, in
    their order: the first sure one wins, an unsure one is kept while the
    next modules may still be sure. Each module is asked the song's first
    LANGUAGE_MAX_QUERIES (artist, title) of _lyrics_queries() until it answers
    - a module raising (no network) is asked again next time."""
    credited, _ = _multi_artist_query(song)
    search = LanguageSearch()
    for module_id, module_name, module in modules:
        found, failed = None, False
        for artist, title in _lyrics_queries(song)[:LANGUAGE_MAX_QUERIES]:
            record_invocation(module_id, LANGUAGES)
            try:
                result = module.get_language(composed(artist), composed(title), songs=[song])
            except Exception as e:
                flog(f"[{module_name}] failed while looking up the language of '{artist} - {title}': {e}")
                failed = True
                break
            found = _language_match(result, song, artist, module_id, module_name, credited)
            if found is not None or getattr(module, "LOCAL", False) or getattr(module, "BY_SONG", False):
                break  # a local module reads the song itself, whatever its names
        if found is None:
            (search.failed if failed else search.missed).append(module_id)
            continue
        record_success(module_id, LANGUAGES)
        if search.match is None or (found.sure and not search.match.sure):
            search.match = found
        if search.match.sure:
            return search
    return search


@dataclass
class GenreMatch:
    """Genres a module found for a song: `genres` as stored (at most
    GENRE_MAX_PER_SONG, MusicBrainz's genre list only). sure: a song's or
    album's genres from a source that may be - never an artist's, never a
    never_sure source's."""
    result: GenreDiscoveryResult
    genres: str
    module_id: str
    sure: bool


@dataclass
class GenreSearch:
    """What discover_genre() found: the best match (None = nothing), the
    modules that answered without one and those that couldn't be asked."""
    match: GenreMatch | None = None
    missed: list = field(default_factory=list)
    failed: list = field(default_factory=list)


def _genre_match(result, song, queried_artist: str, module_id: str, module_name: str,
                 credited: list[str] | None) -> GenreMatch | None:
    """The answer checked against the song, or None when it's another song's
    or names no genre MusicBrainz knows ("Soundtrack", "Worldwide")."""
    if not isinstance(result, GenreDiscoveryResult) or not result.genres:
        return None
    genres = clean_genres(", ".join(canonical_genre(name) for name in result.genres if is_known_genre(name)))
    if not genres:
        flog(f"[{module_name}] none of {result.genres} is a genre MusicBrainz knows, discarding")
        return None
    genres = ", ".join(genres.split(", ")[:GENRE_MAX_PER_SONG])
    sure = result.level != GENRE_LEVEL_ARTIST and not result.never_sure
    if result.checked:
        return GenreMatch(result, genres, module_id, sure)
    if not result.matched_artist:
        flog(f"[{module_name}] genres without the artist they belong to, discarding")
        return None
    if result.matched_title and not any(title_resembles(title, result.matched_title) for title in song_titles(song)):
        slog(f"[{module_name}] the genres of '{result.matched_title}' aren't '{song.title}'s")
        return None
    if not _matched_artist_resembles(queried_artist, result.matched_artist, credited):
        slog(f"[{module_name}] the genres of a song by '{result.matched_artist}' aren't '{queried_artist}'s")
        return None
    return GenreMatch(result, genres, module_id, sure)


def discover_genre(song, modules) -> GenreSearch:
    """The genres the given get_genre() modules find for `song`, in their
    order: the first sure match wins; an unsure one is kept, and after it
    only the modules that may be sure (no GENRE_NEVER_SURE) are asked. Each
    module is asked the song's first GENRE_MAX_QUERIES (artist, title) of
    _lyrics_queries() - not a cover's original's: a cover's genre is its own
    - until it answers; one asking about the song itself (GENRE_BY_SONG) once. A
    module raising (no network) is asked again next time."""
    credited, _ = _multi_artist_query(song)
    covered = getattr(song, "covered_artist", None)
    queries = [(artist, title) for artist, title in _lyrics_queries(song)
               if covered is None or artist != covered.name][:GENRE_MAX_QUERIES]
    search = GenreSearch()
    for module_id, module_name, module in modules:
        if search.match is not None and getattr(module, "GENRE_NEVER_SURE", False):
            continue
        found, failed = None, False
        for artist, title in queries:
            record_invocation(module_id, GENRES)
            try:
                result = module.get_genre(composed(artist), composed(title), songs=[song])
            except Exception as e:
                flog(f"[{module_name}] failed while looking up the genre of '{artist} - {title}': {e}")
                failed = True
                break
            found = _genre_match(result, song, artist, module_id, module_name, credited)
            if found is not None or getattr(module, "GENRE_BY_SONG", False):
                break
        if found is None:
            (search.failed if failed else search.missed).append(module_id)
            continue
        record_success(module_id, GENRES)
        if search.match is None or (found.sure and not search.match.sure):
            search.match = found
        if search.match.sure:
            return search
    if search.match is None:
        flog(f"No module found a genre for '{song.artist.name} - {song.title}'")
    return search
