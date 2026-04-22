from utils.text_utils import truncate_at_word
import importlib.util
import sys
from pathlib import Path

def get_album_name(artist, title):
    print("WORK IN PROGRESS")
    return None

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
        modules.append((script_name, module))
    
    return modules

def discover_album_name(art, son, modules):
    art = truncate_at_word(art)
    son = truncate_at_word(son)
    art_cln = art.split("(")[0].strip()
    son_cln = son.split("(")[0].strip()

    for script_name, module in modules:
        print(f"Looking for it in {script_name}")
        result = module.get_album_name(art_cln, son_cln)
        if result:
            return result
    return None
