"""What the sharing setup wizard (utils/ui/sharing_wizard.py) knows and checks,
without asking anything: the settings the sharing server needs (saved in .env
like Settings -> Server -> "Folders and network"), whether a value can be
used, what to suggest, and the state of the things that aren't settings -
the addresses the server will listen on, the first copy, the systemd
service, lingering, whether the server answers. How a phone reaches one of
those addresses (the home network, a VPN, ...) is the user's business: the
texts describe the options, they don't recommend one. Every function takes what it touches (the .env file, the commands)
as an argument, so tests never reach this machine's real .env or systemd."""
import getpass
import ipaddress
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from config.constants import SHARING_HTTP_PORT
from settings import VPN_OFF, VPN_TAILSCALE, listen_addresses, vpn_choice
from utils.common.env_file import read_env_value
from utils.database.sharing_server import interface_ipv4, tailscale_ipv4, vpn_interface_ipv4


@dataclass(frozen=True)
class SettingStep:
    name: str
    title: str
    required: bool
    purpose: str
    # What it means to leave it empty
    without: str


# In this order: the sharing folder is checked against the drive's mount point
SETTING_STEPS = [
    SettingStep(
        "SHARING_DRIVE_MOUNT", "External drive for the sharing folder", False,
        "Only when the sharing folder will be on an external (USB) drive: the drive's mount point, "
        "e.g. /media/<you>/<drive>. MusicDatabase and the server then write there only while the drive "
        "is mounted. When it's unplugged, the mount point is an empty folder on the system disk - "
        "anything written into it would quietly fill the system disk instead of the drive.",
        "the sharing folder is on this PC's own disk.",
    ),
    SettingStep(
        "SHARING_DIR", "Sharing folder", True,
        "The folder the sharing server gives to Leina Radio: copies of music.db, tag.db and files.db, "
        "plus Users/ (the accounts and their playlists - the originals, not copies), Inbox/ (requests "
        "sent from phones) and MoodChanges/.",
        "no server for phones: no users, no syncing, no requests from Leina Radio.",
    ),
    SettingStep(
        "SHARING_LAN_INTERFACE", "Home network interface", False,
        "The network interface of your home network (Wi-Fi or cable). The server listens on its "
        "address, so a phone on the same network reaches this PC.",
        "the server listens only on the addresses of the next two steps - it needs at least one "
        "address a phone can reach.",
    ),
    SettingStep(
        "SHARING_VPN", "VPN to listen on", False,
        "Only if phones should reach this PC through a VPN, from any provider: which one the server "
        "also listens on - the VPN's network interface by name (e.g. wg0 for WireGuard, tun0 for "
        "OpenVPN), \"tailscale\" (Tailscale's address, found by itself), or \"off\".",
        "no VPN - the server listens on no VPN's address.",
    ),
    SettingStep(
        "SHARING_ADDRESSES", "Other addresses to listen on", False,
        "Exact IPv4 addresses of this PC the server listens on as well, comma-separated - e.g. the "
        "address of another network interface or VPN. 0.0.0.0 means every network this PC is "
        "connected to: anyone who can reach this PC could download the catalog and your music "
        "files - only if you really want that.",
        "only the addresses of the steps before.",
    ),
    SettingStep(
        "MUSIC_LIBRARY_DIR", "Your music library", False,
        "The folder with your own music files (mp3, flac, ...). It's indexed into files.db, so Leina "
        "Radio can play a song's file from this PC (the source \"Serwer\"). An smb://server/share/path/ "
        "library can be indexed for MusicDatabase but isn't served to phones.",
        "phones play only their own files and YouTube.",
    ),
]


def value_warning(name: str, value: str, env_file: Path | None = None, local_addresses=None) -> str | None:
    """Why `value` probably won't work for `name` (None when it looks fine or
    is empty). The external drive is read from env_file, so save
    SHARING_DRIVE_MOUNT before checking SHARING_DIR."""
    if not value:
        return None
    if name == "SHARING_LAN_INTERFACE":
        if not os.path.exists(f"/sys/class/net/{value}"):
            return f"There's no network interface {value} on this PC."
        return None
    if name == "SHARING_VPN":
        return _vpn_warning(value, env_file)
    if name == "SHARING_ADDRESSES":
        return _addresses_warning(value, local_addresses)
    if "://" in value and not value.startswith("file://"):
        if name == "MUSIC_LIBRARY_DIR":
            return None
        return f"{name} must be a local folder."
    path = value.removeprefix("file://")
    if not os.path.isabs(path):
        return f"{path} isn't a full path (it should start with /)."
    if name == "SHARING_DRIVE_MOUNT" and not os.path.ismount(path):
        return f"{path} isn't a mount point right now (is the drive plugged in?)."
    if name == "SHARING_DIR":
        mount = read_env_value("SHARING_DRIVE_MOUNT", env_file)
        if mount and not _inside(path, mount):
            return f"{path} isn't on the external drive ({mount}): nothing is written there while it isn't mounted."
        # A missing folder is offered to be created after saving
        if not os.path.exists(path) and can_create_sharing_dir(path, env_file):
            return None
    if not os.path.isdir(path):
        return f"{path} isn't a folder right now."
    if name == "SHARING_DIR" and not os.access(path, os.W_OK):
        return f"{path} can't be written by this user."
    return None


_NOT_VPN = ("docker", "br-", "veth")


def _vpn_warning(value: str, env_file: Path) -> str | None:
    choice = vpn_choice(value)
    if choice in (VPN_TAILSCALE, VPN_OFF):
        return None
    if not os.path.exists(f"/sys/class/net/{choice}"):
        return f"There's no network interface {choice} on this PC (is the VPN connected?)."
    if choice == read_env_value("SHARING_LAN_INTERFACE", env_file):
        return f"{choice} is the home network interface (step before), not a VPN."
    if choice.startswith(_NOT_VPN):
        return f"{choice} is a container bridge, not a VPN - it must never be exposed."
    return None


def vpn_candidates(env_file: Path | None = None, addresses=None) -> list[tuple[str, str]]:
    """Interfaces that could be a VPN's, to offer for SHARING_VPN: not the
    home network's, never a docker / bridge one, with a private or
    100.64.0.0/10 address (what VPNs hand out)."""
    lan = read_env_value("SHARING_LAN_INTERFACE", env_file)
    found = []
    for name, address in (addresses or local_ipv4_addresses)():
        ip = ipaddress.IPv4Address(address)
        if name == lan or name.startswith(_NOT_VPN) or name in {n for n, _ in found}:
            continue
        if ip.is_private or ip in ipaddress.IPv4Network("100.64.0.0/10"):
            found.append((name, address))
    return found


def _addresses_warning(value: str, local_addresses=None) -> str | None:
    problems = []
    for address in listen_addresses(value):
        try:
            ipaddress.IPv4Address(address)
        except ValueError:
            problems.append(f"{address} isn't an IPv4 address.")
            continue
        if address == "0.0.0.0":
            problems.append("0.0.0.0 listens on every network this PC is connected to - anyone who can "
                            "reach this PC could download the catalog and the music files.")
        elif address not in {a for _, a in (local_addresses or local_ipv4_addresses)()}:
            problems.append(f"{address} isn't an address of this PC right now (the server waits until it is).")
    return " ".join(problems) or None


def address_candidates(env_file: Path | None = None, addresses=None) -> list[tuple[str, str]]:
    """This PC's addresses worth offering for SHARING_ADDRESSES: not the ones
    already chosen, not the LAN interface's or the VPN's (their own steps),
    never a docker / bridge one (sharing_server's rule for the LAN too)."""
    chosen = set(listen_addresses(read_env_value("SHARING_ADDRESSES", env_file)))
    lan = read_env_value("SHARING_LAN_INTERFACE", env_file)
    vpn = vpn_choice(read_env_value("SHARING_VPN", env_file))
    covered = ("tailscale",) if vpn == VPN_TAILSCALE else ()
    return [(name, address) for name, address in (addresses or local_ipv4_addresses)()
            if address not in chosen and name not in (lan, vpn)
            and not name.startswith(_NOT_VPN + covered)]


def local_ipv4_addresses(run=subprocess.run) -> list[tuple[str, str]]:
    """(interface, IPv4 address) of every address this PC has, loopback left out."""
    try:
        out = run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        parts = line.split()
        if "inet" not in parts or len(parts) < 2:
            continue
        try:
            address = ipaddress.IPv4Interface(parts[parts.index("inet") + 1]).ip
        except (ValueError, IndexError):
            continue
        if not address.is_loopback:
            found.append((parts[1], str(address)))
    return found


def _inside(path: str, folder: str) -> bool:
    path, folder = os.path.abspath(path), os.path.abspath(folder)
    return os.path.commonpath([path, folder]) == folder


def can_create_sharing_dir(path: str, env_file: Path | None = None) -> bool:
    """Whether a missing sharing folder may be created: on the system disk
    always, on the external drive only while it's mounted - never under an
    empty mount point."""
    if "://" in path:
        return False
    mount = read_env_value("SHARING_DRIVE_MOUNT", env_file)
    return not mount or (os.path.ismount(mount) and _inside(path, mount))


def mounted_drives(user: str | None = None, roots=("/media", "/run/media", "/mnt")) -> list[str]:
    """Mount points of removable drives (/media/<user>/<drive> and the like)."""
    user = user or getpass.getuser()
    found = []
    for root in roots:
        for base in (os.path.join(root, user), root):
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                path = os.path.join(base, name)
                if os.path.ismount(path) and path not in found:
                    found.append(path)
    return found


def lan_interfaces(net_dir: str = "/sys/class/net", address_of=interface_ipv4) -> list[tuple[str, str]]:
    """(name, private IPv4 address) of each interface that could be the home
    network's; docker0 / br-* bridges and Tailscale never (see sharing_server)."""
    try:
        names = sorted(os.listdir(net_dir))
    except OSError:
        return []
    found = []
    for name in names:
        if name == "lo" or name.startswith(("docker", "br-", "veth", "tailscale")):
            continue
        address = address_of(name)
        if address:
            found.append((name, address))
    return found


def suggestion(name: str, env_file: Path | None = None, home: Path | None = None,
               interfaces=None) -> str | None:
    """A sensible value to offer for `name`, or None."""
    home = home or Path.home()
    if name == "SHARING_DIR":
        mount = read_env_value("SHARING_DRIVE_MOUNT", env_file)
        return str(Path(mount) / "MusicDatabase-Shared") if mount else str(home / "MusicDatabase-Shared")
    if name == "SHARING_LAN_INTERFACE":
        found = (interfaces or lan_interfaces)()
        return found[0][0] if len(found) == 1 else None
    if name == "MUSIC_LIBRARY_DIR":
        music = home / "Music"
        return str(music) if music.is_dir() else None
    return None


def restart_needed(env_file: Path | None = None, environ=os.environ) -> list[str]:
    """The settings whose .env value isn't the one this process started with
    (settings.py reads .env once, at startup)."""
    return [step.name for step in SETTING_STEPS
            if read_env_value(step.name, env_file) != (environ.get(step.name) or "").strip()]


# ==================== checks (not settings) ====================

def listening_plan(env_file: Path | None = None, tailscale_address=tailscale_ipv4,
                   interface_address=interface_ipv4, vpn_address=vpn_interface_ipv4) -> list[tuple[str, str | None]]:
    """(what, address now or None) for every socket the server would open with
    the settings in env_file - the same choice as sharing_server.address_sources()."""
    plan = []
    lan = read_env_value("SHARING_LAN_INTERFACE", env_file)
    if lan:
        plan.append((f"home network ({lan})", interface_address(lan)))
    vpn = vpn_choice(read_env_value("SHARING_VPN", env_file))
    if vpn == VPN_TAILSCALE:
        plan.append(("VPN (Tailscale)", tailscale_address()))
    elif vpn != VPN_OFF:
        plan.append((f"VPN ({vpn})", vpn_address(vpn)))
    for address in listen_addresses(read_env_value("SHARING_ADDRESSES", env_file)):
        plan.append(("chosen address", address))
    return plan


def linger_enabled(run=subprocess.run, user: str | None = None) -> bool | None:
    """Whether systemd starts this user's services at boot, without a login
    (None when loginctl can't tell)."""
    try:
        out = run(["loginctl", "show-user", user or getpass.getuser(), "--property=Linger", "--value"],
                  capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    value = (out.stdout or "").strip()
    return {"yes": True, "no": False}.get(value)


def enable_linger(run=subprocess.run) -> str | None:
    """Turn lingering on for this user; the error message, or None when it
    worked. Not captured: polkit may ask for the password in the terminal."""
    try:
        out = run(["loginctl", "enable-linger"], timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return None if out.returncode == 0 else f"loginctl enable-linger ended with code {out.returncode}"


def service_active(name: str, run=subprocess.run) -> bool:
    try:
        out = run(["systemctl", "--user", "is-active", f"{name}.service"],
                  capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return (out.stdout or "").strip() == "active"


# What the server answered a HEAD /music.db with
SERVING, NO_COPY, DATA_MISSING, NOT_ANSWERING = "serving", "no copy yet", "folder unavailable", "not answering"


def server_answer(address: str, port: int = SHARING_HTTP_PORT, opener=urllib.request.urlopen) -> str:
    request = urllib.request.Request(f"http://{address}:{port}/music.db", method="HEAD")
    try:
        with opener(request, timeout=5):
            return SERVING
    except urllib.error.HTTPError as exc:
        return {404: NO_COPY, 503: DATA_MISSING}.get(exc.code, f"HTTP {exc.code}")
    except (urllib.error.URLError, OSError):
        return NOT_ANSWERING


def phone_addresses(plan: list[tuple[str, str | None]], port: int = SHARING_HTTP_PORT) -> list[tuple[str, str]]:
    """What may go into Leina Radio -> Ustawienia -> Serwer: (field, address).
    The home network's address is for "sieć lokalna"; any other one the phone
    can reach from elsewhere is a candidate for "połączenie zdalne"."""
    lines = []
    for what, address in plan:
        if not address or address == "0.0.0.0":
            continue
        field = "Adres serwera (sieć lokalna)" if what.startswith("home network") else "Adres serwera (połączenie zdalne)"
        lines.append((field, f"http://{address}:{port}/"))
    return lines
