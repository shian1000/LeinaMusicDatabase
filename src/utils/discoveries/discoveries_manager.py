from utils.common.text_utils import truncate_at_word, is_blacklisted_album, similarity, scaled_similarity_threshold
from utils.discoveries.discovery_result import DiscoveryResult
from config.constants import SPELLING_CHECK_THRESHOLD
import importlib.util
import sys
from pathlib import Path
from utils.common.debug import slog

def load_discovery_modules():
    current_dir = Path(__file__).parent
    modules_dir = current_dir / "discovery_modules"
    
    modules = []
    for script_path in sorted(modules_dir.glob("*.py")):
        script_name = script_path.stem
        spec = importlib.util.spec_from_file_location(script_name, script_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[script_name] = module
        spec.loader.exec_module(module)

        module_name = getattr(module, "MODULE_NAME", script_name)  # fallback to filename if missing
        modules.append((module_name, module))
    
    return modules


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


def _call_module(module, module_name: str, artist: str, title: str) -> str | None:
    """Run a module's get_album_name, isolated from crashes and bad output."""
    try:
        result = module.get_album_name(artist, title)
    except Exception as e:
        slog(f"[{module_name}] crashed while looking up '{artist} - {title}': {e}")
        print(f"{module_name} failed unexpectedly, skipping it for this song")
        return None
    return _validate_result(result, artist, title, module_name)


def discover_album_name(song, modules):
    art, son, alb = song.artist.name, song.title, song.album
    slog(f"{art} - {son} ({alb})")
    art = truncate_at_word(art)
    son = truncate_at_word(son)
    art_cln = art.split("(")[0].strip()
    son_cln = son.split("(")[0].strip()

    slog("About to lunch modules' loop")
    for module_name, module in modules:
        print(f"Looking in {module_name} module")
        album = _call_module(module, module_name, art_cln, son_cln)

        #Repeat the searching if there is a synonym for an artist
        if not album and song.artist.synonyms is not None:
            print(f"Looking for it in {module_name} using synonyms")
            album = _call_module(module, module_name, song.artist.synonyms, son_cln)

        if album:
            return album
    slog("Gave up =)")
    return None
