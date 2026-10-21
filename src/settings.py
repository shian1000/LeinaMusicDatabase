# settings.py
from dataclasses import dataclass, field
from upath import UPath
from dotenv import load_dotenv
import os

from utils.common.env_file import ENV_FILE
from utils.database.database_location import load_database_dir_override

load_dotenv(ENV_FILE)


def _default_database_dir() -> UPath:
    override = load_database_dir_override()
    if override:
        return UPath(override)
    return UPath(__file__).parent / "database"


def _env_path(name: str) -> UPath | None:
    value = os.getenv(name, "").strip()
    return UPath(value) if value else None


# This machine's folders and network interface come from .env (see
# .env.example). A missing key is never replaced by someone's real path: the
# feature that needs it is skipped, the same as when its drive isn't plugged in.
_sharing_dir = _env_path("SHARING_DIR")


@dataclass
class Settings:
    database_dir: UPath = _default_database_dir()
    config_dir: UPath = UPath(__file__).parent / "config"
    music_database_dir: UPath = database_dir / UPath("music.db")
    # The mp3 collection (MUSIC_LIBRARY_DIR): a local folder, or an smb:// URI
    # without credentials - SMB_USERNAME / SMB_PASSWORD are added when it's used.
    # Empty = no library: indexing and serving its files are skipped.
    local_library_dir_str: str = os.getenv("MUSIC_LIBRARY_DIR", "").strip()
    local_library_dir: UPath | None = UPath(local_library_dir_str) if local_library_dir_str else None
    smb_username: str = os.getenv("SMB_USERNAME")
    smb_password: str = os.getenv("SMB_PASSWORD")
    # A YouTube Data API key (Google Cloud console -> Credentials -> API key,
    # "YouTube Data API v3"), only for reading public video data such as
    # whether a video still exists. Unlike the OAuth token it doesn't expire.
    youtube_api_key: str | None = os.getenv("YOUTUBE_API_KEY")
    export_dir: UPath = UPath(__file__).parent.parent / "import"
    # Where music.db / tag.db are copied for the mobile app to download
    # (utils/database/sharing.py; SHARING_DIR). It may live on an external
    # drive (SHARING_DRIVE_MOUNT), which is only there while mounted - the
    # mount is checked before writing. With SHARING_DRIVE_MOUNT empty it's on
    # the system disk and only has to exist (sharing_dir_available()).
    # SHARING_DIR missing = sharing, users, the inbox and mood changes are
    # all unavailable, as with an unplugged drive.
    sharing_drive_mount: UPath | None = _env_path("SHARING_DRIVE_MOUNT") if _sharing_dir else None
    sharing_dir: UPath | None = _sharing_dir
    # One folder per user (utils/database/users.py): their private user.db
    # (playlists, private tags) and account.json. The originals live here, on
    # the data drive - unlike sharing_dir's music.db/tag.db, these aren't copies.
    users_dir: UPath | None = sharing_dir / "Users" if sharing_dir else None
    # Requests from the phone (utils/database/inbox_requests.py): the sharing
    # server writes them into new/, "Resolve new requests" moves them to processed/.
    phone_inbox_dir: UPath | None = sharing_dir / "Inbox" if sharing_dir else None
    # Changes to global moods sent from the phone by users allowed to make
    # them (utils/database/global_mood_log.py): the sharing server writes one
    # file per change into pending/, MusicDatabase applies them to tag.db and
    # appends them to history.jsonl.
    global_mood_changes_dir: UPath | None = sharing_dir / "MoodChanges" if sharing_dir else None
    # Network interface the sharing server also listens on, for the phone on
    # the home Wi-Fi (SHARING_LAN_INTERFACE; named, not "first private
    # address": docker0/br-* are private too). Empty = Tailscale only.
    sharing_lan_interface: str = os.getenv("SHARING_LAN_INTERFACE", "").strip()
    # The public copy of the catalog (utils/database/public_database.py):
    # "owner/repo" whose GitHub Release it goes to, and a fine-grained token
    # with Contents: read and write on that repo only. Never print or log it.
    github_db_repo: str = os.getenv("GITHUB_DB_REPO", "").strip()
    github_db_token: str = os.getenv("GITHUB_DB_TOKEN", "").strip()


def sharing_dir_available(mount, folder) -> bool:
    """Whether the sharing folder (SHARING_DIR) can be used right now. On an
    external drive (`mount` set): only while it's mounted - never create
    folders under an empty mount point, they'd land on the system disk. On
    the system disk (`mount` None): the folder exists and is writable."""
    if folder is None:
        return False
    if mount is not None:
        return os.path.ismount(str(mount))
    return os.path.isdir(str(folder)) and os.access(str(folder), os.W_OK)


def drive_unavailable_message(mount, folder=None) -> str:
    """Why the sharing folder (SHARING_DIR, maybe on the drive at
    SHARING_DRIVE_MOUNT) can't be used right now."""
    if folder is None and mount is None:
        return "SHARING_DIR isn't set - Settings -> Server -> Folders and network (or .env)"
    if mount is None:
        return (f"The sharing folder {folder} doesn't exist or can't be written - create it, "
                "or set another one in Settings -> Server -> Folders and network")
    return f"Drive {mount} is not mounted - is the external drive plugged in?"


settings = Settings()  # single instance
