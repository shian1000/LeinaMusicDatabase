# settings.py
from dataclasses import dataclass, field
from upath import UPath
from dotenv import load_dotenv
import os

from utils.database.database_location import load_database_dir_override

load_dotenv()


def _default_database_dir() -> UPath:
    override = load_database_dir_override()
    if override:
        return UPath(override)
    return UPath(__file__).parent / "database"


@dataclass
class Settings:
    database_dir: UPath = _default_database_dir()
    config_dir: UPath = UPath(__file__).parent / "config"
    music_database_dir: UPath = database_dir / UPath("music.db")
    local_library_dir_str: str = "/media/owner/ServerHDD/Shared/Music/"
    local_library_dir: UPath = UPath(local_library_dir_str)
    smb_username: str = os.getenv("SMB_USERNAME")
    smb_password: str = os.getenv("SMB_PASSWORD")
    smb_local_library_dir: UPath = UPath(f"smb://{smb_username}:{smb_password}@server.local/Shared/Music/")
    export_dir: UPath = UPath(__file__).parent.parent / "import"


settings = Settings()  # single instance



