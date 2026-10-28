"""Manage database -> "Import a request package": a .zip of Leina Radio's
requests sent by hand (utils/database/request_packages.py) into the inbox,
then, if wanted, straight on to "Resolve new requests"."""

from pathlib import Path

import questionary

from utils.database.request_packages import PackageError, import_package
from utils.ui.inbox_request_review import resolve_new_requests
from utils.ui.prompts import ask_yes_no
from utils.common.output import error, warn

_TYPE_PATH = "Type a path"
_RECENT_LIMIT = 10


def _recent_zips() -> list[Path]:
    downloads = Path.home() / "Downloads"
    try:
        zips = [p for p in downloads.iterdir() if p.is_file() and p.suffix.lower() == ".zip"]
    except OSError:
        return []
    return sorted(zips, key=lambda p: p.stat().st_mtime, reverse=True)[:_RECENT_LIMIT]


def _pick_package() -> Path | None:
    recent = _recent_zips()
    if recent:
        choices = [questionary.Choice(title=p.name, value=p) for p in recent]
        choices += [questionary.Choice(title=_TYPE_PATH, value=_TYPE_PATH), questionary.Choice(title="Back", value=None)]
        picked = questionary.select("Request package (newest in Downloads first):", choices=choices).ask()
        if picked != _TYPE_PATH:
            return picked
    typed = questionary.path("Path of the .zip:", default=str(Path.home()) + "/").ask()
    return Path(typed).expanduser() if typed and typed.strip() else None


def import_request_package() -> None:
    path = _pick_package()
    if path is None:
        return
    if not path.is_file():
        error(f"{path} isn't a file.")
        return
    try:
        result = import_package(path)
    except (PackageError, OSError) as exc:
        error(f"Package not imported: {exc}")
        return
    print(f"Added {len(result.added)} request file(s) to the inbox.")
    for name in result.already_there:
        warn(f"{name} is in the inbox already (imported before?) - skipped.")
    if result.added and ask_yes_no("Resolve the new requests now?"):
        resolve_new_requests()
