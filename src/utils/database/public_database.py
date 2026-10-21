"""
Publishing the public copy of music.db and tag.db (public_export.py) as the
assets of one GitHub Release, for Leina Radio phones that can't reach the
sharing server (app ADR 0014, handoff musicdatabase-public-database.md).

- The release has the fixed tag PUBLIC_DB_RELEASE_TAG in GITHUB_DB_REPO
  (.env), so phones download
  https://github.com/<repo>/releases/download/database/<file>. Created the
  first time, never marked "latest".
- Assets: music.db, tag.db and version.json - the last one uploaded last, so
  it never names files that aren't there yet. Each database goes up under a
  temporary name and is renamed over the old one, so the old file is missing
  only between a delete and a rename.
- Only when changed: version.json carries each file's content_sha256
  (public_export.content_digest()); an export whose digests equal the
  published ones uploads nothing. Asked from the menu, the owner can publish
  anyway.
- Two call sites: main.py once a day after the backup when the daily publish
  is on (publish_daily()), and Settings -> Server -> "Public database on
  GitHub" (publish()).
- The switch and the last result are in PUBLIC_DB_STATE_FILE.
"""

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from config.constants import (
    PUBLIC_DB_FORMAT,
    PUBLIC_DB_RELEASE_NAME,
    PUBLIC_DB_RELEASE_TAG,
    PUBLIC_DB_STATE_FILE,
    PUBLIC_DB_VERSION_FILE,
)
from settings import Settings
from utils.common.debug import flog, mlog
from utils.database.backup import DB_FILES
from utils.database.github_release import GithubError, GithubReleaseClient
from utils.database.public_export import ExportError, export_public_databases

PUBLISHED = "published"
UNCHANGED = "unchanged"
SKIPPED = "skipped"  # not set up
FAILED = "failed"

_UPLOADING_SUFFIX = ".uploading"
_RELEASE_BODY = (
    "The public copy of LeinaMusicDatabase's catalog for Leina Radio: music.db and tag.db, "
    "with version.json listing their sizes and SHA-256. Replaced automatically when the catalog "
    "changes - download the assets by their fixed URLs, not this release's files list."
)


@dataclass
class PublishResult:
    status: str
    message: str
    url: str | None = None


# ==================== settings and last result ====================

def _state_path() -> Path:
    return Path(PUBLIC_DB_STATE_FILE)


def _load_state() -> dict:
    try:
        value = json.loads(_state_path().read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(path)


def daily_publish_enabled() -> bool:
    return _load_state().get("daily", True)


def set_daily_publish(enabled: bool) -> None:
    state = _load_state()
    state["daily"] = bool(enabled)
    _save_state(state)


def last_result() -> dict | None:
    """{"at", "status", "message"} of the last attempt, or None."""
    return _load_state().get("last")


def _record(result: PublishResult) -> PublishResult:
    state = _load_state()
    state["last"] = {"at": datetime.now().astimezone().isoformat(timespec="seconds"),
                     "status": result.status, "message": result.message}
    _save_state(state)
    return result


def missing_setup() -> list[str]:
    """The .env keys still to fill in; [] when publishing can be tried."""
    missing = []
    if "/" not in Settings.github_db_repo.strip("/"):
        missing.append("GITHUB_DB_REPO")
    if not Settings.github_db_token:
        missing.append("GITHUB_DB_TOKEN")
    return missing


def download_url(name: str) -> str | None:
    repo = Settings.github_db_repo.strip("/")
    return f"https://github.com/{repo}/releases/download/{PUBLIC_DB_RELEASE_TAG}/{name}" if repo else None


# ==================== publishing ====================

def version_document(export, published_at: datetime) -> dict:
    return {
        "format": PUBLIC_DB_FORMAT,
        "published_at": published_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": {f.name: {"size": f.size, "sha256": f.sha256, "content_sha256": f.content_sha256}
                  for f in export.files},
    }


def _published_version(client, release) -> dict | None:
    asset = release.assets.get(PUBLIC_DB_VERSION_FILE)
    if asset is None:
        return None
    try:
        value = json.loads(client.download_asset(asset))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _unchanged(export, published: dict | None, release) -> bool:
    if not published or published.get("format") != PUBLIC_DB_FORMAT:
        return False
    files = published.get("files") or {}
    for f in export.files:
        entry = files.get(f.name) or {}
        # A database deleted from the release by hand is published again
        if entry.get("content_sha256") != f.content_sha256 or f.name not in release.assets:
            return False
    return set(files) == {f.name for f in export.files}


def _replace_asset(client, release, name: str, path: Path, content_type: str) -> None:
    temporary = name + _UPLOADING_SUFFIX
    client.delete_asset(release, temporary)  # left by an interrupted publish
    uploaded = client.upload_asset(release, temporary, path, content_type)
    client.delete_asset(release, name)
    client.rename_asset(release, uploaded, name)


def publish(force: bool = False, client=None, sources=None, now=None) -> PublishResult:
    """Build, check and publish the public databases. `force` uploads even
    when nothing changed. Never raises for an expected failure (not set up,
    no network, the check failed): the result says why, and is recorded."""
    if client is None:
        missing = missing_setup()
        if missing:
            return _record(PublishResult(SKIPPED, f"not set up: {' and '.join(missing)} missing in .env "
                                                  "(Settings -> Server -> Public database on GitHub -> How to set it up)"))
        client = GithubReleaseClient(Settings.github_db_repo.strip("/"), Settings.github_db_token)
    with tempfile.TemporaryDirectory(prefix="musicdatabase-public-") as folder:
        try:
            export = export_public_databases(sources or DB_FILES, Path(folder))
        except ExportError as exc:
            flog(f"Public database not published - the export failed its check: {exc}")
            return _record(PublishResult(FAILED, f"the export failed its check, nothing was published: {exc}"))
        try:
            release = client.get_release(PUBLIC_DB_RELEASE_TAG)
            if release is None:
                release = client.create_release(PUBLIC_DB_RELEASE_TAG, PUBLIC_DB_RELEASE_NAME, _RELEASE_BODY)
            if not force and _unchanged(export, _published_version(client, release), release):
                return _record(PublishResult(UNCHANGED, "the catalog hasn't changed since the last publish",
                                             release.html_url))
            for f in export.files:
                _replace_asset(client, release, f.name, f.path, "application/vnd.sqlite3")
            version_path = Path(folder) / PUBLIC_DB_VERSION_FILE
            version_path.write_text(json.dumps(version_document(export, now or datetime.now(timezone.utc)), indent=2))
            _replace_asset(client, release, PUBLIC_DB_VERSION_FILE, version_path, "application/json")
        except GithubError as exc:
            flog(f"Public database not published: {exc}")
            return _record(PublishResult(FAILED, str(exc)))
    sizes = ", ".join(f"{f.name} {f.size / 1024:.0f} KiB" for f in export.files)
    mlog(f"Public database published to {release.html_url}: {sizes}")
    return _record(PublishResult(PUBLISHED, f"published {sizes}", release.html_url))


def publish_daily() -> PublishResult | None:
    """The daily publish (main.py, after the daily backup): None when it's
    switched off; otherwise any failure is recorded and returned, never
    raised, so it never blocks opening the app. Tried again the next day."""
    if not daily_publish_enabled():
        return None
    try:
        return publish()
    except Exception as exc:  # a disk error, ... - startup must go on
        flog(f"Public database publish failed: {type(exc).__name__}: {exc}")
        return _record(PublishResult(FAILED, f"{type(exc).__name__}: {exc}"))
