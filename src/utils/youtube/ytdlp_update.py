"""Keeping yt-dlp current. YouTube changes its defences often and an old
yt-dlp runs into rate limiting and the "confirm you're not a bot" wall much
sooner (2026-10-13), so main.py checks once a day, after the daily backup,
whether PyPI has a newer one and offers to install it
(utils/ui/ytdlp_update_prompt.py).

Installed as `yt-dlp[default]` - yt-dlp-ejs (YouTube's JS challenge
scripts) and friends come with it; `deno`, the JS runtime they run on, is a
separate pinned package in the same venv. After an update every line of
requirements.txt is re-pinned to what's now installed, so the file keeps
matching the venv."""

import importlib
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import requests
from packaging.version import InvalidVersion, Version

from config.constants import YTDLP_PYPI_URL, YTDLP_UPDATE_CHECK_TIMEOUT
from utils.common.debug import slog

REQUIREMENTS_FILE = Path(__file__).resolve().parents[3] / "requirements.txt"
_PIN_RE = re.compile(r"^([A-Za-z0-9_.\-]+)==(\S+)\s*$")


def installed_version(package: str = "yt-dlp") -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def latest_version(timeout: float = YTDLP_UPDATE_CHECK_TIMEOUT) -> str | None:
    """The newest yt-dlp on PyPI, or None when PyPI can't be reached."""
    try:
        response = requests.get(YTDLP_PYPI_URL, timeout=timeout)
        response.raise_for_status()
        return response.json()["info"]["version"]
    except Exception as exc:
        slog(f"yt-dlp update check failed (non-fatal): {exc}", priority=3)
        return None


def available_update() -> tuple[str, str] | None:
    """(installed, newest) when PyPI has a newer yt-dlp; None when it's
    current or either version is unknown."""
    installed, latest = installed_version(), latest_version()
    if not installed or not latest:
        return None
    try:
        # Version() also reads 2026.08.19 and 2026.8.19 as the same
        return (installed, latest) if Version(latest) > Version(installed) else None
    except InvalidVersion:
        return None


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def repin_requirements(path: Path = REQUIREMENTS_FILE) -> list[str]:
    """Rewrite every `name==version` line whose package is now installed in
    another version. Returns "name old -> new" for each line changed."""
    changed, lines = [], []
    for line in path.read_text().splitlines():
        match = _PIN_RE.match(line)
        if match:
            name, pinned = match.groups()
            now = installed_version(name)
            if now and now != pinned:
                line = f"{name}=={now}"
                changed.append(f"{name} {pinned} -> {now}")
        lines.append(line)
    path.write_text("\n".join(lines) + "\n")
    return changed


def install_ytdlp(version: str) -> list[str]:
    """pip-install yt-dlp[default]==version into this venv, then re-pin
    requirements.txt. Raises subprocess.CalledProcessError if pip fails
    (requirements.txt is left alone then)."""
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", f"yt-dlp[default]=={version}"],
        check=True, capture_output=True, text=True,
    )
    # So this process sees the versions pip just installed
    importlib.invalidate_caches()
    return repin_requirements()
