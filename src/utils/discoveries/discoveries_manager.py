from utils.common.text_utils import truncate_at_word
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
        result = module.get_album_name(art_cln, son_cln)
        #Repeat the searching if there is a synonym for an artist
        if not result:
            if not song.artist.synonyms is None:
                print(f"Looking for it in {module_name} using synonyms")
                result = module.get_album_name(song.artist.synonyms, son_cln)
        if result:
            return result
    slog("Gave up =)")
    return None
