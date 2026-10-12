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
    # The mp3 collection. Moved 2026-10-12 from the HDD (ServerHDD/Shared/Music,
    # the old copy is still there for now) to the T7 SSD, next to sharing_dir.
    local_library_dir_str: str = "/media/owner/T7/Shared/Music/Library/"
    local_library_dir: UPath = UPath(local_library_dir_str)
    smb_username: str = os.getenv("SMB_USERNAME")
    smb_password: str = os.getenv("SMB_PASSWORD")
    smb_local_library_dir: UPath = UPath(f"smb://{smb_username}:{smb_password}@server.local/Shared (SSD)/Music/Library/")
    export_dir: UPath = UPath(__file__).parent.parent / "import"
    # Where music.db / tag.db are copied for the mobile app to download
    # (utils/database/sharing.py). Lives on the external T7 drive, which is
    # only there while mounted - sharing_drive_mount is checked before writing.
    sharing_drive_mount: UPath = UPath("/media/owner/T7")
    sharing_dir: UPath = sharing_drive_mount / "Shared/Music/Database"
    # One folder per user (utils/database/users.py): their private user.db
    # (playlists, private tags) and account.json. The originals live here, on
    # T7 - unlike sharing_dir's music.db/tag.db, these aren't copies.
    users_dir: UPath = sharing_dir / "Users"
    # Network interface the sharing server also listens on, for the phone on
    # the home Wi-Fi (named, not "first private address": docker0/br-* are
    # private too). Empty = Tailscale only.
    sharing_lan_interface: str = "enp2s0"


settings = Settings()  # single instance



