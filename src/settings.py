# settings.py
from dataclasses import dataclass, field
from upath import UPath
from dotenv import load_dotenv
import os

load_dotenv()

@dataclass
class Settings:
    database_dir: UPath = UPath(__file__).parent.parent / "database"
    config_dir: UPath = UPath(__file__).parent.parent / "config"
    music_database_dir: UPath = database_dir / UPath("music.db")
    local_library_dir_str: str = "smb://server.local/Shared/Music/"
    # local_library_dir_str: str = "/home/owner/Documents/Code/MusicDatabase/"
    local_library_dir: UPath = UPath(local_library_dir_str)
    smb_username: str = os.getenv("SMB_USERNAME")
    smb_password: str = os.getenv("SMB_PASSWORD")
    smb_local_library_dir: UPath = UPath(f"smb://{smb_username}:{smb_password}@server.local/Shared/Music/")
    export_dir: UPath = UPath(__file__).parent.parent / "import"


settings = Settings()  # single instance



