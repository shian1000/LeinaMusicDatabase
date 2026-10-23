"""Which of main.py's start-up tasks run (Settings -> "Start-up tasks"): one
switch each in .env, all on by default - an empty or missing key is on, "off"
turns it off, so a .env from before the switches behaves as it did. Read when
the app starts, from the file (not the environment), so a change counts from
the next start without a restart in between.

The daily publish to GitHub keeps its own switch (public_database.py's state
file, also in Settings -> Server -> Public database on GitHub); the start-up
screen shows and changes that one instead of adding a second.

Never switchable, for the data's consistency or because nothing could run
without them: waiting for the app lock, the fresh-install database choice,
migrations (with their own backup), the album backfill after its migration,
applying song id changes, and resuming the sharing setup."""
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from config.constants import STARTUP_STATE_FILE
from utils.common.env_file import read_env_value, save_env_value

OFF = "off"
# With the daily backup off, every start warns once the newest backup is older
BACKUP_WARNING_AGE = timedelta(days=7)


@dataclass(frozen=True)
class StartupSwitch:
    key: str
    label: str
    explanation: str


BACKUP = StartupSwitch(
    "STARTUP_BACKUP", "Daily backup",
    "The first start of a day copies music.db, tag.db, files.db and the users' databases into "
    "src/database/archive/. Turned off, nothing is backed up except right before a database upgrade - "
    "a mistake, a bad import or a broken disk can't be undone from a recent copy; every start then "
    "warns once the newest backup is more than 7 days old.",
)
MOOD_CHANGES = StartupSwitch(
    "STARTUP_MOOD_CHANGES", "Global mood changes from Leina Radio",
    "Applies the changes to global moods that permitted users made in Leina Radio. Turned off, they "
    "wait in the sharing folder (Users -> Moods -> Changes from Leina Radio).",
)
LIBRARY_INDEX = StartupSwitch(
    "STARTUP_LIBRARY_INDEX", "Daily music library index",
    "Once a day: new, changed and removed files of the music library into files.db "
    "(Settings -> Server -> Index music library does it by hand).",
)
SHARE_COPY = StartupSwitch(
    "STARTUP_SHARE_COPY", "Daily copy for Leina Radio",
    "Once a day: refreshes the copies of the databases Leina Radio downloads from this PC "
    "(Settings -> Server -> Submit database for sharing does it by hand).",
)
YTDLP_UPDATE = StartupSwitch(
    "STARTUP_YTDLP_UPDATE", "Daily yt-dlp update offer",
    "Once a day: asks whether to install a newer yt-dlp (an old one gets blocked by YouTube sooner).",
)
MESSAGES = StartupSwitch(
    "STARTUP_MESSAGES", "Start-up messages",
    "What the automatic request check changed since the last start, and how many new requests "
    "from Leina Radio wait in the inbox.",
)

SWITCHES = [BACKUP, MOOD_CHANGES, LIBRARY_INDEX, SHARE_COPY, YTDLP_UPDATE, MESSAGES]


def enabled(switch: StartupSwitch, env_file: Path | None = None) -> bool:
    return read_env_value(switch.key, env_file).lower() != OFF


def set_enabled(switch: StartupSwitch, on: bool, env_file: Path | None = None) -> None:
    # On is the default: the key is removed rather than written as "on"
    save_env_value(switch.key, "" if on else OFF, env_file)


def save_choices(on_keys, env_file: Path | None = None) -> list[StartupSwitch]:
    """Set every switch from the keys ticked on; the switches that changed."""
    changed = []
    for switch in SWITCHES:
        on = switch.key in on_keys
        if on != enabled(switch, env_file):
            set_enabled(switch, on, env_file)
            changed.append(switch)
    return changed


ALWAYS = ("waiting for the automatic request check to finish, the database choice on a fresh install, "
          "database upgrades (migrations, with a backup right before) and linking songs to albums after "
          "one, keeping tags, playlists and the library index pointing at merged or deleted songs, and "
          "continuing the sharing setup after its restart")


# ==================== the first start of a day ====================

def _load_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def first_start_today(today: date | None = None, state_file: Path | str = STARTUP_STATE_FILE) -> bool:
    """Whether the once-a-day tasks haven't run today yet; marks them as run.
    With the daily backup on, its new backup decides instead (main.py has
    always done that) - this marker is for when it's off."""
    path = Path(state_file)
    today = (today or date.today()).isoformat()
    state = _load_state(path)
    if state.get("daily_tasks") == today:
        return False
    mark_daily_tasks_run(today, path)
    return True


def mark_daily_tasks_run(today: str | None = None, state_file: Path | str = STARTUP_STATE_FILE) -> None:
    path = Path(state_file)
    state = _load_state(path)
    state["daily_tasks"] = today or date.today().isoformat()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(path)
    except OSError:
        pass  # at worst the daily tasks run again at the next start


def backup_warning(newest: datetime | None, now: datetime | None = None) -> str | None:
    """The warning every start prints while the daily backup is off."""
    now = now or datetime.now()
    if newest is None:
        return ("The daily backup is off and there's no backup of the databases at all - "
                "Settings -> Server -> Back up database now, or turn it on in Settings -> Start-up tasks.")
    age = now - newest
    if age <= BACKUP_WARNING_AGE:
        return None
    return (f"The daily backup is off and the newest backup is {age.days} days old ({newest:%Y-%m-%d}) - "
            "Settings -> Server -> Back up database now, or turn it on in Settings -> Start-up tasks.")
