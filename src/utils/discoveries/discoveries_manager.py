from utils.common.text_utils import (
    truncate_at_word,
    is_blacklisted_album,
    is_soundtrack_artist,
    similarity,
    scaled_similarity_threshold,
)
from utils.discoveries.discovery_result import DiscoveryResult, YearDiscoveryResult
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
from config.constants import SPELLING_CHECK_THRESHOLD, MIN_PLAUSIBLE_RELEASE_YEAR
from datetime import datetime
import ast
import importlib.util
import sys
from pathlib import Path
from utils.common.debug import slog

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


def _validate_result(result, queried_artist: str, queried_title: str, module_name: str) -> str | None:
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
        slog(f"[{module_name}] returned unexpected type {type(result).__name__}, discarding")
        return None

    if not isinstance(album, str) or not album.strip():
        slog(f"[{module_name}] returned an empty/non-string album, discarding")
        return None
    album = album.strip()

    if is_blacklisted_album(album):
        slog(f"[{module_name}] returned blacklisted album '{album}', discarding")
        return None

    # If the module told us what it actually matched, independently verify
    # that resembles what we searched for. This is the check that catches a
    # module confidently returning data for the wrong song.
    if matched_title:
        threshold = scaled_similarity_threshold(queried_title, matched_title, SPELLING_CHECK_THRESHOLD)
        title_sim = similarity(queried_title, matched_title)
        if title_sim < threshold:
            slog(f"[{module_name}] matched title '{matched_title}' doesn't resemble query "
                 f"'{queried_title}' (sim={title_sim:.2f} < {threshold:.2f}), discarding")
            return None

    if matched_artist:
        threshold = scaled_similarity_threshold(queried_artist, matched_artist, SPELLING_CHECK_THRESHOLD)
        artist_sim = similarity(queried_artist, matched_artist)
        if artist_sim < threshold:
            slog(f"[{module_name}] matched artist '{matched_artist}' doesn't resemble query "
                 f"'{queried_artist}' (sim={artist_sim:.2f} < {threshold:.2f}), discarding")
            return None

    return album


def _call_module(module, module_id: str, module_name: str, artist: str, title: str) -> str | None:
    """Run a module's get_album_name, isolated from crashes and bad output.

    Counts as one invocation for the Statistics menu even if this is a retry
    of the same module with untruncated text or an artist synonym -- each
    such retry is a real call to get_album_name().
    """
    record_invocation(module_id)
    try:
        result = module.get_album_name(artist, title)
    except Exception as e:
        slog(f"[{module_name}] crashed while looking up '{artist} - {title}': {e}")
        print(f"{module_name} failed unexpectedly, skipping it for this song")
        return None
    album = _validate_result(result, artist, title, module_name)
    if album:
        record_success(module_id)
    return album


def discover_album_name(song, modules):
    art_full, son_full, alb = song.artist.name, song.title, song.album
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

    slog("About to lunch modules' loop")
    for module_id, module_name, module in modules:
        print(f"Looking in {module_name} module")
        album = _call_module(module, module_id, module_name, art_cln, son_cln)

        # truncate_at_word() strips trailing "feat. X" / "& X" credits, but it
        # can also cut into a legitimate title/artist (e.g. "Bang, Bang").
        # If the truncated search came up empty, retry with the untruncated
        # strings before giving up on this module.
        if not album and truncated:
            print(f"Looking in {module_name} using untruncated title/artist")
            album = _call_module(module, module_id, module_name, art_cln_full, son_cln_full)

        # Both attempts above still strip parentheses from title/artist. If
        # that stripping actually removed something, retry once more with
        # the fully raw strings before giving up on this module.
        if not album and raw_differs:
            print(f"Looking in {module_name} using full title/artist including parentheses")
            album = _call_module(module, module_id, module_name, art_full, son_full)

        #Repeat the searching if there is a synonym for an artist
        if not album and song.artist.synonyms is not None:
            print(f"Looking for it in {module_name} using synonyms")
            album = _call_module(module, module_id, module_name, song.artist.synonyms, son_cln)

        if album:
            return album

    # Game/anime soundtracks are often stored with the album in the artist
    # field ("Tekken 5 OST") and no fetcher knows the individual track. Only a
    # last resort - a fetcher may still find the proper album name - and not
    # recorded as any fetcher's success in the Statistics menu.
    if is_soundtrack_artist(art_full):
        print(f"No fetcher found an album; artist looks like a soundtrack, using it as the album: {art_full}")
        return art_full

    slog("Gave up =)")
    return None


def _validate_year_result(result, queried_artist: str, queried_query: str, module_name: str) -> int | None:
    """Same defensive sanity-checking as _validate_result(), adapted for a
    release year: rejects crashy/wrong-typed/implausible years, and
    independently re-checks any reported matched_title/matched_artist
    against what was actually searched for before trusting the year."""
    if result is None:
        return None

    if isinstance(result, YearDiscoveryResult):
        year, matched_title, matched_artist = result.year, result.matched_title, result.matched_artist
    elif isinstance(result, int):
        year, matched_title, matched_artist = result, None, None
    else:
        slog(f"[{module_name}] returned unexpected type {type(result).__name__} for a release year, discarding")
        return None

    current_year = datetime.now().year
    if not isinstance(year, int) or not (MIN_PLAUSIBLE_RELEASE_YEAR <= year <= current_year + 1):
        slog(f"[{module_name}] returned implausible release year '{year}', discarding")
        return None

    if matched_title:
        threshold = scaled_similarity_threshold(queried_query, matched_title, SPELLING_CHECK_THRESHOLD)
        title_sim = similarity(queried_query, matched_title)
        if title_sim < threshold:
            slog(f"[{module_name}] matched title '{matched_title}' doesn't resemble query "
                 f"'{queried_query}' (sim={title_sim:.2f} < {threshold:.2f}), discarding")
            return None

    if matched_artist:
        threshold = scaled_similarity_threshold(queried_artist, matched_artist, SPELLING_CHECK_THRESHOLD)
        artist_sim = similarity(queried_artist, matched_artist)
        if artist_sim < threshold:
            slog(f"[{module_name}] matched artist '{matched_artist}' doesn't resemble query "
                 f"'{queried_artist}' (sim={artist_sim:.2f} < {threshold:.2f}), discarding")
            return None

    return year


def _call_year_module(module, module_id: str, module_name: str, artist: str, query: str, is_single: bool) -> int | None:
    """Run a module's get_release_year(), isolated from crashes and bad output.
    Mirrors _call_module(), but counted in the separate year stats file."""
    record_year_invocation(module_id)
    try:
        result = module.get_release_year(artist, query, is_single)
    except Exception as e:
        slog(f"[{module_name}] crashed while looking up release year for '{artist} - {query}': {e}")
        print(f"{module_name} failed unexpectedly, skipping it for this lookup")
        return None
    year = _validate_year_result(result, artist, query, module_name)
    if year:
        record_year_success(module_id)
    return year


def discover_release_year(artist: str, query: str, is_single: bool, modules, artist_synonyms: str | None = None) -> int | None:
    """Look up a release year for either an album (query = album title,
    is_single=False) or a standalone single (query = song title,
    is_single=True), trying each enabled year-capable module in order until
    one returns a validated year.

    Mirrors discover_album_name()'s truncation/fallback stages, but operates
    directly on an (artist, query) pair rather than a Song object, since the
    caller may be querying once on behalf of several songs that share an
    album.
    """
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
        year = _call_year_module(module, module_id, module_name, art_cln, qry_cln, is_single)

        if not year and truncated:
            print(f"Looking for release year in {module_name} using untruncated artist/query")
            year = _call_year_module(module, module_id, module_name, art_cln_full, qry_cln_full, is_single)

        if not year and raw_differs:
            print(f"Looking for release year in {module_name} using full artist/query including parentheses")
            year = _call_year_module(module, module_id, module_name, art_full, qry_full, is_single)

        if not year and artist_synonyms:
            print(f"Looking for release year in {module_name} using synonyms")
            year = _call_year_module(module, module_id, module_name, artist_synonyms, qry_cln, is_single)

        if year:
            return year
    slog("Gave up looking for a release year =)")
    return None
