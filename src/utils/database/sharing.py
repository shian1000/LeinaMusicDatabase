"""
Copy music.db, tag.db and files.db (the music library index) into the
sharing folder (Settings.sharing_dir) that the MusicDatabaseApp mobile
client downloads from, plus installing the systemd user service that serves
that folder over Tailscale (sharing_server.py) and the timer that runs the
automatic pass over the phone's requests (auto_resolve_requests.py).

Contract the app relies on (see docs/runbooks/database.md -> "Sharing with
the mobile app"):
- <sharing_dir>/music.db, tag.db and files.db are always complete SQLite
  files - each is written to a temp file next to it and os.replace()d in, so
  a download in progress never sees half a file;
- all come from the same moment - every temp copy is made before any
  is swapped in.

Two call sites:
- main.py, once a day after the daily backup and library scan (share_databases_quietly()).
- Settings -> "Submit database for sharing" (share_databases()).
"""

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from config.constants import (
    INBOX_AUTO_INTERVAL,
    INBOX_AUTO_SERVICE_NAME,
    INBOX_AUTO_STARTUP_DELAY,
    PHONE_INBOX_LIMIT_BYTES,
    PHONE_REQUEST_MAX_BYTES,
    SHARING_HTTP_PORT,
    SHARING_SERVICE_NAME,
    SHARING_STARTUP_DELAY,
)
from settings import Settings, drive_unavailable_message, sharing_dir_available
from utils.common.debug import mlog, slog
from utils.database.backup import DB_FILES
from utils.database.files_index import local_library_path

SHARING_DIR = Path(str(Settings.sharing_dir)) if Settings.sharing_dir else None
SHARING_DRIVE_MOUNT = Path(str(Settings.sharing_drive_mount)) if Settings.sharing_drive_mount else None
SHARING_LAN_INTERFACE = Settings.sharing_lan_interface
SHARING_TAILSCALE = Settings.sharing_tailscale
SHARING_ADDRESSES = list(Settings.sharing_addresses)
PHONE_INBOX_DIR = Path(str(Settings.phone_inbox_dir)) if Settings.phone_inbox_dir else None
USERS_DIR = Path(str(Settings.users_dir)) if Settings.users_dir else None
GLOBAL_MOOD_CHANGES_DIR = Path(str(Settings.global_mood_changes_dir)) if Settings.global_mood_changes_dir else None
SERVER_SCRIPT = Path(__file__).with_name("sharing_server.py")
REPO_ROOT = Path(__file__).resolve().parents[3]
INBOX_AUTO_SCRIPT = REPO_ROOT / "auto_resolve_requests.py"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"

# Must never be named music.db / tag.db - the server would serve a half-written file.
_TMP_SUFFIX = ".tmp"


class SharingUnavailableError(Exception):
    """The sharing folder can't be written right now (e.g. the data drive isn't plugged in)."""


def _ensure_sharing_dir() -> None:
    # Never mkdir under an unmounted mount point: the folder would land on the
    # system disk instead of the external drive. Without a drive the folder
    # itself must exist - the owner creates it, so a typo doesn't.
    if not sharing_dir_available(SHARING_DRIVE_MOUNT, SHARING_DIR):
        raise SharingUnavailableError(drive_unavailable_message(SHARING_DRIVE_MOUNT, SHARING_DIR))
    SHARING_DIR.mkdir(parents=True, exist_ok=True)


def _copy_to_tmp(src_path: Path, tmp_path: Path) -> None:
    tmp_path.unlink(missing_ok=True)
    src_conn = sqlite3.connect(str(src_path))
    dest_conn = sqlite3.connect(str(tmp_path))
    try:
        with dest_conn:
            src_conn.backup(dest_conn)
    finally:
        src_conn.close()
        dest_conn.close()


def share_databases() -> list[Path]:
    """
    Copy every existing DB file into SHARING_DIR and return the published
    paths. Uses SQLite's online backup API (like backup.py), so it's safe
    while the app has the database open.

    Raises SharingUnavailableError when the drive isn't mounted; other
    errors (disk full, ...) propagate too, after cleaning up temp files.
    """
    _ensure_sharing_dir()
    pending = []
    try:
        for name, src_path in DB_FILES.items():
            if not src_path.exists():
                continue
            dest_path = SHARING_DIR / src_path.name
            tmp_path = SHARING_DIR / (src_path.name + _TMP_SUFFIX)
            _copy_to_tmp(src_path, tmp_path)
            pending.append((tmp_path, dest_path))
        for tmp_path, dest_path in pending:
            os.replace(tmp_path, dest_path)
    finally:
        for tmp_path, _ in pending:
            tmp_path.unlink(missing_ok=True)
    published = [dest for _, dest in pending]
    if published:
        mlog(f"Databases shared to {SHARING_DIR}: {[p.name for p in published]}")
    return published


def share_databases_quietly() -> list[Path]:
    """
    share_databases() for the startup path: any failure is logged and
    swallowed, so a missing drive never blocks opening the app.
    """
    try:
        return share_databases()
    except SharingUnavailableError as exc:
        slog(f"Database sharing skipped: {exc}", priority=3)
    except Exception as exc:
        slog(f"Database sharing failed (non-fatal): {exc}", priority=3)
    return []


# ==================== systemd user service ====================

def _service_unit(python: str) -> str:
    command = f"{python} {SERVER_SCRIPT} --dir {SHARING_DIR}"
    # No --mount: SHARING_DIR is on the system disk
    if SHARING_DRIVE_MOUNT is not None:
        command += f" --mount {SHARING_DRIVE_MOUNT}"
    command += f" --port {SHARING_HTTP_PORT}"
    if SHARING_LAN_INTERFACE:
        command += f" --lan-interface {SHARING_LAN_INTERFACE}"
    # Both left out by default, so a unit written before they existed stays the same
    if not SHARING_TAILSCALE:
        command += " --no-tailscale"
    for address in SHARING_ADDRESSES:
        command += f" --address {address}"
    command += (f" --inbox {PHONE_INBOX_DIR} --max-request-bytes {PHONE_REQUEST_MAX_BYTES}"
                f" --inbox-limit-bytes {PHONE_INBOX_LIMIT_BYTES}")
    command += f" --users {USERS_DIR} --mood-changes {GLOBAL_MOOD_CHANGES_DIR}"
    # The library's files for /library/<files.id>; an smb:// library isn't served
    library = local_library_path()
    if library is not None:
        command += f" --library {library}"
    return f"""[Unit]
Description=MusicDatabase - serve music.db/tag.db/files.db and the music library's files to the mobile app, log it in and take its requests, over Tailscale and the LAN

[Service]
ExecStart={command}
Restart=always
RestartSec=10
"""


def _timer_unit() -> str:
    return f"""[Unit]
Description=Start the MusicDatabase sharing server shortly after login

[Timer]
OnStartupSec={SHARING_STARTUP_DELAY}
Unit={SHARING_SERVICE_NAME}.service

[Install]
WantedBy=timers.target
"""


def _inbox_service_unit(python: str) -> str:
    # The venv's bin first: the YouTube checks run its yt-dlp (and deno)
    path = f"{Path(python).parent}:/usr/local/bin:/usr/bin:/bin"
    return f"""[Unit]
Description=MusicDatabase - settle new requests from the phone on its own where it safely can

[Service]
Type=oneshot
WorkingDirectory={REPO_ROOT}
Environment=PATH={path}
ExecStart={python} {INBOX_AUTO_SCRIPT}
Nice=10
"""


def _inbox_timer_unit() -> str:
    return f"""[Unit]
Description=Run the MusicDatabase automatic request check every {INBOX_AUTO_INTERVAL}

[Timer]
OnStartupSec={INBOX_AUTO_STARTUP_DELAY}
OnUnitActiveSec={INBOX_AUTO_INTERVAL}
Unit={INBOX_AUTO_SERVICE_NAME}.service

[Install]
WantedBy=timers.target
"""


def _systemctl(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=True, capture_output=True, text=True)


def server_python() -> str:
    # The server is stdlib-only, so it runs on the system interpreter and
    # keeps working if the venv is rebuilt.
    return shutil.which("python3", path="/usr/bin:/bin") or sys.executable


def service_unit_path() -> Path:
    return SYSTEMD_USER_DIR / f"{SHARING_SERVICE_NAME}.service"


def service_unit_current() -> bool:
    """Whether the installed server unit is the one install_sharing_service()
    would write now (same folders, interface and library)."""
    path = service_unit_path()
    return path.exists() and path.read_text() == _service_unit(server_python())


def install_sharing_service() -> list[str]:
    """
    Write (or overwrite) the user service + timer, enable the timer and
    (re)start the server now. Idempotent: re-running replaces the units and
    restarts the server on the current script/paths. Returns a log of the
    steps taken, for the menu to print.
    """
    python = server_python()
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    service_path = service_unit_path()
    timer_path = SYSTEMD_USER_DIR / f"{SHARING_SERVICE_NAME}.timer"
    service_path.write_text(_service_unit(python))
    timer_path.write_text(_timer_unit())
    # The automatic pass needs the app's packages: the venv's interpreter
    inbox_service_path = SYSTEMD_USER_DIR / f"{INBOX_AUTO_SERVICE_NAME}.service"
    inbox_timer_path = SYSTEMD_USER_DIR / f"{INBOX_AUTO_SERVICE_NAME}.timer"
    inbox_service_path.write_text(_inbox_service_unit(sys.executable))
    inbox_timer_path.write_text(_inbox_timer_unit())
    steps = [f"Wrote {path}" for path in (service_path, timer_path, inbox_service_path, inbox_timer_path)]

    _systemctl("daemon-reload")
    _systemctl("enable", f"{SHARING_SERVICE_NAME}.timer")
    steps.append(f"Enabled {SHARING_SERVICE_NAME}.timer (starts the server {SHARING_STARTUP_DELAY} after login)")
    _systemctl("restart", f"{SHARING_SERVICE_NAME}.service")
    steps.append(f"Started {SHARING_SERVICE_NAME}.service now")
    _systemctl("enable", "--now", f"{INBOX_AUTO_SERVICE_NAME}.timer")
    steps.append(f"Enabled {INBOX_AUTO_SERVICE_NAME}.timer (checks new requests every {INBOX_AUTO_INTERVAL})")
    return steps


def sharing_service_disable_hint() -> str:
    units = [f"{name}.{kind}" for name in (SHARING_SERVICE_NAME, INBOX_AUTO_SERVICE_NAME)
             for kind in ("timer", "service")]
    return (
        f"systemctl --user disable --now {' '.join(units)}\n"
        f"rm {' '.join(f'{SYSTEMD_USER_DIR}/{unit}' for unit in units)}\n"
        f"systemctl --user daemon-reload"
    )
