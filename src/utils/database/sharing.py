"""
Copy music.db and tag.db into the sharing folder (Settings.sharing_dir) that
the MusicDatabaseApp mobile client downloads from, plus installing the
systemd user service that serves that folder over Tailscale
(sharing_server.py).

Contract the app relies on (see docs/runbooks/database.md -> "Sharing with
the mobile app"):
- <sharing_dir>/music.db and <sharing_dir>/tag.db are always complete SQLite
  files - each is written to a temp file next to it and os.replace()d in, so
  a download in progress never sees half a file;
- both come from the same moment - both temp copies are made before either
  is swapped in.

Two call sites:
- main.py, right after a successful daily backup (share_databases_quietly()).
- Settings -> "Submit database for sharing" (share_databases()).
"""

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from config.constants import SHARING_HTTP_PORT, SHARING_SERVICE_NAME, SHARING_STARTUP_DELAY
from settings import Settings
from utils.common.debug import mlog, slog
from utils.database.backup import DB_FILES

SHARING_DIR = Path(str(Settings.sharing_dir))
SHARING_DRIVE_MOUNT = Path(str(Settings.sharing_drive_mount))
SHARING_LAN_INTERFACE = Settings.sharing_lan_interface
SERVER_SCRIPT = Path(__file__).with_name("sharing_server.py")
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"

# Must never be named music.db / tag.db - the server would serve a half-written file.
_TMP_SUFFIX = ".tmp"


class SharingUnavailableError(Exception):
    """The sharing folder can't be written right now (e.g. the T7 drive isn't plugged in)."""


def _ensure_sharing_dir() -> None:
    # Never mkdir under an unmounted mount point: the folder would land on the
    # system disk instead of the external drive.
    if not os.path.ismount(SHARING_DRIVE_MOUNT):
        raise SharingUnavailableError(
            f"Drive {SHARING_DRIVE_MOUNT} is not mounted - is the external drive plugged in?"
        )
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
    command = f"{python} {SERVER_SCRIPT} --dir {SHARING_DIR} --mount {SHARING_DRIVE_MOUNT} --port {SHARING_HTTP_PORT}"
    if SHARING_LAN_INTERFACE:
        command += f" --lan-interface {SHARING_LAN_INTERFACE}"
    return f"""[Unit]
Description=MusicDatabase - serve music.db/tag.db to the mobile app over Tailscale and the LAN

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


def _systemctl(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=True, capture_output=True, text=True)


def install_sharing_service() -> list[str]:
    """
    Write (or overwrite) the user service + timer, enable the timer and
    (re)start the server now. Idempotent: re-running replaces the units and
    restarts the server on the current script/paths. Returns a log of the
    steps taken, for the menu to print.
    """
    # The server is stdlib-only, so it runs on the system interpreter and
    # keeps working if the venv is rebuilt.
    python = shutil.which("python3", path="/usr/bin:/bin") or sys.executable
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    service_path = SYSTEMD_USER_DIR / f"{SHARING_SERVICE_NAME}.service"
    timer_path = SYSTEMD_USER_DIR / f"{SHARING_SERVICE_NAME}.timer"
    service_path.write_text(_service_unit(python))
    timer_path.write_text(_timer_unit())
    steps = [f"Wrote {service_path}", f"Wrote {timer_path}"]

    _systemctl("daemon-reload")
    _systemctl("enable", f"{SHARING_SERVICE_NAME}.timer")
    steps.append(f"Enabled {SHARING_SERVICE_NAME}.timer (starts the server {SHARING_STARTUP_DELAY} after login)")
    _systemctl("restart", f"{SHARING_SERVICE_NAME}.service")
    steps.append(f"Started {SHARING_SERVICE_NAME}.service now")
    return steps


def sharing_service_disable_hint() -> str:
    return (
        f"systemctl --user disable --now {SHARING_SERVICE_NAME}.timer {SHARING_SERVICE_NAME}.service\n"
        f"rm {SYSTEMD_USER_DIR}/{SHARING_SERVICE_NAME}.service {SYSTEMD_USER_DIR}/{SHARING_SERVICE_NAME}.timer\n"
        f"systemctl --user daemon-reload"
    )
