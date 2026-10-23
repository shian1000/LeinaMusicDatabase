"""This machine's own settings in the repo's .env (gitignored; every key is
described in .env.example): read by settings.py at startup, changed from
Settings -> Server -> "Folders and network". A change takes effect after the
app is restarted, and reaches the sharing server only when "Setup this PC for
database sharing" rewrites its systemd unit."""
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, set_key, unset_key

from config.constants import PROJECT_ROOT

# Looked up when a function runs, never bound as a default argument, so the
# tests' swap of ENV_FILE (tests/conftest.py) reaches every caller
ENV_FILE = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class MachineKey:
    name: str
    label: str
    is_path: bool


MACHINE_KEYS = [
    MachineKey("MUSIC_LIBRARY_DIR", "Music library", True),
    MachineKey("SHARING_DRIVE_MOUNT", "External drive mount point (empty = none)", True),
    MachineKey("SHARING_DIR", "Sharing folder", True),
    MachineKey("SHARING_LAN_INTERFACE", "LAN interface", False),
    MachineKey("SHARING_VPN", "VPN to listen on too (tailscale / off / an interface, empty = none)", False),
    MachineKey("SHARING_ADDRESSES", "Other addresses to listen on (comma-separated)", False),
]


def read_env_value(name: str, env_file: Path | None = None) -> str:
    """The value saved in .env ("" when unset), not the one this process started with."""
    env_file = env_file or ENV_FILE
    if not env_file.exists():
        return ""
    return (dotenv_values(env_file).get(name) or "").strip()


def save_env_value(name: str, value: str, env_file: Path | None = None) -> None:
    """Set name in .env, keeping every other line; an empty value removes the key."""
    env_file = env_file or ENV_FILE
    value = value.strip()
    if not env_file.exists():
        if not value:
            return
        env_file.touch(mode=0o600)
    if value:
        # Quoted only when a "#" would start a comment: MusicDatabase-Remote.sh reads .env with sed
        set_key(env_file, name, value, quote_mode="always" if "#" in value else "never")
    elif name in dotenv_values(env_file):
        unset_key(env_file, name)
