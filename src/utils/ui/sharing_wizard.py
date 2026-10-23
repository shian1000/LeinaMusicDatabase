"""The step-by-step setup of this PC as the server for Leina Radio (Settings ->
Server -> "Setup this PC for database sharing" offers it). Each setting is
explained, shown with its current value and a suggestion, checked, and saved
to .env the way "Folders and network" saves it. Nothing happens on its own
(the owner's rule): every write or command - saving a value, creating the
folder, the first copy, a user, the service, lingering, the restart - is a
yes/no question, a "no" skips it and the summary lists it as not done; only
reading and checking go unasked. Stopping at any step keeps what was saved
and changes nothing else, and running it again starts from the saved values.
settings.py reads .env only at startup, so after a change the app offers to
restart itself (main.py CONTINUE_SETUP_FLAG) and run the checks on the new
values. The logic is in utils/common/sharing_setup.py."""
import getpass
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import questionary

from config.constants import INBOX_AUTO_SERVICE_NAME, SHARING_HTTP_PORT, SHARING_SERVICE_NAME
from settings import listen_addresses
from utils.common import sharing_setup as setup
from utils.common.env_file import read_env_value, save_env_value
from utils.ui.menu_utils import open_file_browser_terminal
from utils.ui.prompts import ask_yes_no

CONTINUE_SETUP_FLAG = "--continue-sharing-setup"
MAIN_SCRIPT = Path(__file__).resolve().parents[3] / "main.py"

YELLOW, RED, GREEN, BOLD, RESET = "\033[93m", "\033[91m", "\033[92m", "\033[1m", "\033[0m"

_KEEP, _SUGGESTED, _TYPE, _BROWSE, _CLEAR, _STOP = (
    "__keep__", "__suggested__", "__type__", "__browse__", "__clear__", "__stop__")
_ADD = "__add__:"


class SetupStopped(Exception):
    """The user stopped the setup (or pressed Ctrl+C)."""


class Report:
    """The actions the checks didn't do, for the summary."""

    def __init__(self):
        self.not_done: list[str] = []

    def skip(self, what: str) -> None:
        self.not_done.append(what)


def _select(message: str, choices: list) -> str | None:
    return questionary.select(message, choices=choices).ask()


def _text(message: str, default: str = "") -> str | None:
    return questionary.text(message, default=default).ask()


def _yes(message: str) -> bool:
    answer = ask_yes_no(message)
    if answer is None:
        raise SetupStopped
    return answer


def _say(text: str, colour: str = "") -> None:
    for paragraph in text.split("\n"):
        wrapped = textwrap.fill(paragraph, width=96) if paragraph else ""
        print(f"{colour}{wrapped}{RESET if colour else ''}")


# ==================== settings ====================

def _choices(step, current: str, suggested: str | None, env_file: Path) -> list:
    if current:
        keep = f"Keep it: {current}"
    elif step.required:
        keep = "Leave it unset for now"
    else:
        keep = "Keep it empty"
    choices = [questionary.Choice(title=keep, value=_KEEP)]
    if suggested and suggested != current:
        choices.append(questionary.Choice(title=f"Use the suggestion: {suggested}", value=_SUGGESTED))
    if step.name == "SHARING_DRIVE_MOUNT":
        choices += [questionary.Choice(title=f"This drive: {path}", value=path) for path in setup.mounted_drives()]
    elif step.name == "SHARING_LAN_INTERFACE":
        choices += [questionary.Choice(title=f"This interface: {name} ({address})", value=name)
                    for name, address in setup.lan_interfaces() if name not in (current, suggested)]
    elif step.name == "SHARING_VPN":
        choices += [questionary.Choice(title=f"This interface: {name} ({address})", value=name)
                    for name, address in setup.vpn_candidates(env_file) if name != current]
        choices += [questionary.Choice(title=title, value=value)
                    for title, value in (("tailscale (Tailscale's address, found by itself)", "tailscale"),
                                         ("off (no VPN)", "off")) if value != current.lower()]
    elif step.name == "SHARING_ADDRESSES":
        choices += [questionary.Choice(title=f"Add this address: {address} ({name})", value=_ADD + address)
                    for name, address in setup.address_candidates(env_file)]
    else:
        choices.append(questionary.Choice(title="Choose a folder in the file browser", value=_BROWSE))
    choices.append(questionary.Choice(title="Type it", value=_TYPE))
    if current and not step.required:
        choices.append(questionary.Choice(title="Clear it (leave it empty)", value=_CLEAR))
    choices.append(questionary.Choice(title="Stop the setup", value=_STOP))
    return choices


def _new_value(step, current: str, suggested: str | None, env_file: Path) -> str | None:
    """The value picked and confirmed for `step` (None = keep the current one)."""
    while True:
        picked = _select(f"{step.name}:", _choices(step, current, suggested, env_file))
        if picked in (None, _STOP):
            raise SetupStopped
        if picked == _KEEP:
            return None
        if picked == _SUGGESTED:
            value = suggested
        elif picked == _CLEAR:
            value = ""
        elif picked == _TYPE:
            value = _text(f"{step.name}:", default=current or suggested or "")
            if value is None:
                raise SetupStopped
        elif picked == _BROWSE:
            chosen = open_file_browser_terminal(current or str(Path.home()))
            if not chosen:
                continue
            value = str(chosen)
        elif picked.startswith(_ADD):
            value = ",".join(listen_addresses(current) + [picked.removeprefix(_ADD)])
        else:
            value = picked
        value = value.strip()
        if value == current:
            return None
        if not value and step.required:
            _say(f"{step.name} is required for sharing - keep it or pick another one.", RED)
            continue
        warning = setup.value_warning(step.name, value, env_file)
        if warning:
            _say(warning, YELLOW)
        if _yes(f"Save {step.name}={value} to .env?" if value else f"Clear {step.name} in .env?"):
            return value


def offer_to_create_sharing_dir(value: str, env_file: Path | None = None) -> bool:
    """Ask before creating a missing sharing folder - the app never creates it
    on its own, so a typo doesn't become a folder. On an external drive it may
    be created only while the drive is mounted. Whether it exists now."""
    if not value or "://" in value:
        return False
    path = value.removeprefix("file://")
    if os.path.exists(path):
        return True
    if not setup.can_create_sharing_dir(path, env_file) or not ask_yes_no(f"{path} doesn't exist yet. Create it?"):
        return False
    try:
        os.makedirs(path)
    except OSError as exc:
        _say(f"Couldn't create it: {exc}", RED)
        return False
    print(f"Created {path}.")
    return True


def _setting_step(number: int, step, env_file: Path) -> None:
    current = read_env_value(step.name, env_file)
    suggested = setup.suggestion(step.name, env_file)
    print(f"\n{BOLD}Step {number}/{len(setup.SETTING_STEPS)}: {step.title} ({step.name}) - "
          f"{'REQUIRED' if step.required else 'OPTIONAL'}{RESET}")
    _say(step.purpose)
    _say(f"Left empty: {step.without}")
    print(f"Now: {current or '(not set)'}")
    if suggested and suggested != current:
        print(f"Suggested: {suggested}")
    value = _new_value(step, current, suggested, env_file)
    if value is None:
        print("Unchanged.")
    else:
        save_env_value(step.name, value, env_file)
        print(f"{GREEN}{step.name} {'set to ' + value if value else 'cleared'} in .env.{RESET}")
    if step.name == "SHARING_DIR":
        offer_to_create_sharing_dir(read_env_value(step.name, env_file), env_file)


def restart_to_continue() -> None:
    """Start MusicDatabase again in this terminal, straight into the checks."""
    from utils.database.database_sessions import close_global_database_sessions

    close_global_database_sessions(commit=True)
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, str(MAIN_SCRIPT), CONTINUE_SETUP_FLAG])


def _print_listening(env_file: Path) -> list:
    plan = setup.listening_plan(env_file)
    if not plan:
        _say("With these settings the server has no address to listen on: set the home network "
             "interface or an address to listen on - a phone must be able to reach one of them.", RED)
        return plan
    print(f"The server will listen on (port {SHARING_HTTP_PORT}):")
    for what, address in plan:
        shown = "every address of this PC" if address == "0.0.0.0" else (address or "no address right now")
        print(f"  {what}: {shown}")
    _say("A phone must be able to reach one of them - on the same home network, over a VPN, or "
         "any other way that leads to this PC.")
    return plan


# ==================== checks ====================

def _check_first_copy(settings, report: Report) -> None:
    from utils.database.sharing import SharingUnavailableError, share_databases

    print(f"\n{BOLD}The databases for the phones{RESET} - REQUIRED")
    copy = Path(str(settings.sharing_dir)) / "music.db"
    if copy.exists():
        _say(f"A copy is there ({copy}). MusicDatabase refreshes it once a day when it starts, "
             "and with Settings -> Server -> Submit database for sharing.", GREEN)
        return
    _say("No copy in the sharing folder yet - phones would get nothing.", YELLOW)
    if not _yes("Copy music.db, tag.db and files.db there now?"):
        report.skip("the first copy of the databases (Settings -> Server -> Submit database for sharing)")
        return
    try:
        published = share_databases()
    except (SharingUnavailableError, OSError) as exc:
        _say(f"Not copied: {exc}", RED)
        report.skip("the first copy of the databases (failed)")
        return
    if not published:
        _say("There are no databases to copy yet.", YELLOW)
        report.skip("the first copy of the databases (nothing to copy yet)")
    for path in published:
        print(f"  {path}")


def _check_users(report: Report) -> None:
    from utils.database.users import list_usernames, users_available, users_unavailable_message

    print(f"\n{BOLD}A user for the phone{RESET} - OPTIONAL")
    _say("Leina Radio logs in with a MusicDatabase user (on the phone: Ustawienia -> Konto). Without one "
         "the phone still downloads the catalog, but nothing syncs (playlists, moods, liked songs, "
         "listening stats) and it can't send requests or corrections.")
    not_done = "a user for the phone (main menu -> Users -> Create a new user)"
    if not users_available():
        _say(f"Users can't be made yet: {users_unavailable_message()}", YELLOW)
        report.skip(not_done)
        return
    existing = list_usernames()
    if existing:
        _say(f"Users: {', '.join(existing)}. More in the main menu -> Users.", GREEN)
        return
    if not _yes("No users yet. Create the first one now?"):
        report.skip(not_done)
        return
    from menu.main_menu.users import create_new_user

    create_new_user()
    if not list_usernames():
        report.skip(not_done)


def _check_service(env_file: Path, report: Report) -> None:
    from utils.database.sharing import (
        NoListenAddressError,
        install_sharing_service,
        service_unit_current,
        service_unit_path,
        sharing_service_disable_hint,
    )

    print(f"\n{BOLD}The sharing server (systemd user service {SHARING_SERVICE_NAME}){RESET} - REQUIRED")
    _say(f"It serves the sharing folder on port {SHARING_HTTP_PORT}, starts by itself shortly after "
         f"this user's session starts, and {INBOX_AUTO_SERVICE_NAME} settles phones' requests in the background.")
    current = service_unit_current()
    if current and setup.service_active(SHARING_SERVICE_NAME):
        _say("Installed with the current settings and running.", GREEN)
        return
    if not service_unit_path().exists():
        _say("Not installed yet.", YELLOW)
    elif not current:
        _say("Installed, but with older settings (folders or addresses changed since).", YELLOW)
    else:
        _say("Installed, but not running.", YELLOW)
    if not setup.listening_plan(env_file):
        _say("Not offered: the server would have no address to listen on (see above).", RED)
        report.skip("the sharing server (no address to listen on)")
        return
    if not _yes("Install (or reinstall) and start it now?"):
        report.skip("the sharing server (Settings -> Server -> Setup this PC for database sharing)")
        return
    try:
        steps = install_sharing_service()
    except NoListenAddressError as exc:
        _say(f"Not installed: {exc}", RED)
        report.skip("the sharing server (no address to listen on)")
        return
    except subprocess.CalledProcessError as exc:
        _say(f"Setup failed: {' '.join(exc.cmd)}\n{(exc.stderr or '').strip()}", RED)
        report.skip("the sharing server (failed)")
        return
    except OSError as exc:
        _say(f"Setup failed: {exc}", RED)
        report.skip("the sharing server (failed)")
        return
    for step in steps:
        print(f"  {step}")
    print("To turn it off later:")
    print(sharing_service_disable_hint())


def _check_linger(report: Report) -> None:
    print(f"\n{BOLD}Serving without logging in (loginctl enable-linger){RESET} - OPTIONAL")
    _say("A user service runs only while this user is logged in on the PC. With lingering on, it starts "
         "when the PC boots and keeps running after logging out - what a PC that's left on as a "
         "server needs. Not needed when you're always logged in (or log in automatically).")
    state = setup.linger_enabled()
    if state:
        _say("On.", GREEN)
        return
    if state is None:
        _say("Couldn't check it (loginctl show-user).", YELLOW)
    command = f"sudo loginctl enable-linger {getpass.getuser()}"
    if not _yes("Turn lingering on for this user?"):
        report.skip(f"lingering - only needed to serve without logging in ({command})")
        return
    error = setup.enable_linger()
    if error:
        _say(f"Didn't work: {error}\nRun it yourself: {command}", RED)
        report.skip("lingering (failed)")
    else:
        _say("On.", GREEN)


def _summary(env_file: Path, report: Report) -> None:
    print(f"\n{BOLD}Summary{RESET}")
    for step in setup.SETTING_STEPS:
        value = read_env_value(step.name, env_file)
        mark = f"{GREEN}set{RESET}" if value else (f"{RED}missing{RESET}" if step.required else "empty")
        print(f"  {step.name}: {value or '-'} ({mark})")
    plan = setup.listening_plan(env_file)
    for what, address in plan:
        if address and address != "0.0.0.0":
            answer = setup.server_answer(address)
            colour = GREEN if answer == setup.SERVING else YELLOW
            print(f"  The server on {address} ({what}): {colour}{answer}{RESET}")
    if not plan:
        print(f"  {RED}No address for the server to listen on.{RESET}")
    if report.not_done:
        print(f"\n{YELLOW}Not done:{RESET}")
        for item in report.not_done:
            print(f"  - {item}")
    addresses = setup.phone_addresses(plan)
    if addresses:
        print(f"\n{BOLD}In Leina Radio{RESET}: Ustawienia -> Serwer, an address the phone can reach:")
        for field, address in addresses:
            print(f"  {field}: {address}")
        _say('"Sieć lokalna" is used on the Wi-Fi picked under "Wi-Fi sieci lokalnej" ("Użyj bieżącego '
             'połączenia"), "połączenie zdalne" everywhere else. Then "Zapisz adresy i pobierz bazę", and log '
             "in under Ustawienia -> Konto with a user from the main menu -> Users.")
        _say(f"If the phone can't connect, a firewall on this PC (e.g. ufw) must allow TCP port "
             f"{SHARING_HTTP_PORT} from where the phone connects.")
    print("\nRun this setup again any time: Settings -> Server -> Setup this PC for database sharing.")


def _checks(env_file: Path) -> None:
    from settings import drive_unavailable_message, settings, sharing_dir_available

    report = Report()
    print(f"\n{BOLD}Checks{RESET} - each change is asked first\n")
    _print_listening(env_file)
    if settings.sharing_dir is None:
        _say("\nSHARING_DIR isn't set, so there's no sharing folder for the next steps (the copy, users, "
             "the server) - run the setup again and set it.", RED)
        report.skip("the sharing folder (SHARING_DIR)")
    else:
        if not sharing_dir_available(settings.sharing_drive_mount, settings.sharing_dir):
            _say(drive_unavailable_message(settings.sharing_drive_mount, settings.sharing_dir), YELLOW)
            report.skip("the first copy and users (the sharing folder isn't available)")
        else:
            _check_first_copy(settings, report)
            _check_users(report)
        _check_service(env_file, report)
        _check_linger(report)
    _summary(env_file, report)


def run_sharing_wizard(resume: bool = False, env_file: Path | None = None) -> None:
    try:
        if resume:
            print(f"{BOLD}Setup of this PC for database sharing - continued after the restart{RESET}")
        else:
            print(f"{BOLD}Setup of this PC for database sharing{RESET}")
            _say(f"{len(setup.SETTING_STEPS)} settings first, then checks of what isn't a setting. Nothing "
                 "is done without asking: each change is a question, and \"no\" skips it. \"Stop the "
                 "setup\" (or Ctrl+C) at any step keeps what you saved so far and changes nothing else.")
            for number, step in enumerate(setup.SETTING_STEPS, start=1):
                _setting_step(number, step, env_file)
            print()
            _print_listening(env_file)
            changed = setup.restart_needed(env_file)
            if changed:
                _say(f"\nChanged: {', '.join(changed)}. MusicDatabase reads these when it starts, so the "
                     "checks need a restart.")
                if _yes("Restart MusicDatabase now and continue with the checks?"):
                    restart_to_continue()
                    return
                _say("Not restarted: restart MusicDatabase later and run this setup again for the checks.")
                return
        _checks(env_file)
    except SetupStopped:
        _say("\nSetup stopped. What you saved so far stays, nothing else was changed - run it again any time.",
             YELLOW)
