import json
from pathlib import Path

RECENT_DIRS_FILE = Path("recent_dirs.json")
MAX_RECENT_DIRS = 5

def load_recent_dirs() -> list[str]:
    if not RECENT_DIRS_FILE.exists():
        return []
    return json.loads(RECENT_DIRS_FILE.read_text())

def save_recent_dirs(new_dir: str):
    dirs = load_recent_dirs()
    if new_dir in dirs:
        dirs.remove(new_dir)
    dirs.insert(0, new_dir)
    dirs = dirs[:MAX_RECENT_DIRS]
    RECENT_DIRS_FILE.write_text(json.dumps(dirs, indent=2))