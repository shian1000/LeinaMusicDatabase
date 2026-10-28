"""
A fresh install has no music.db / tag.db: main.py asks once (utils/ui/
first_run_prompt.py) whether to download the public catalog or start empty.

- The public catalog is the copy public_database.py publishes as the GitHub
  Release PUBLIC_DB_RELEASE_TAG of GITHUB_DB_REPO (PUBLIC_CATALOG_DEFAULT_REPO
  when unset): music.db, tag.db and version.json with each file's size and
  SHA-256. Both databases are downloaded next to their final place, checked
  against version.json and as SQLite, and only then moved in - together, so a
  failed download leaves no database behind and the question comes again.
- An empty start creates the current schema through the models
  (create_music_db() / create_tag_db()); main.py's migration run right after
  adopts every migration file, as for any database made that way.
- Either way the startup migrations run on top, so a catalog published by an
  older version catches up like the owner's own database would.
"""

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import requests

from config.constants import (
    PUBLIC_CATALOG_DEFAULT_REPO,
    PUBLIC_CATALOG_MAX_BYTES,
    PUBLIC_CATALOG_TIMEOUT,
    PUBLIC_DB_FORMAT,
    PUBLIC_DB_RELEASE_TAG,
    PUBLIC_DB_VERSION_FILE,
)
from settings import Settings
from utils.common.debug import flog, mlog

CATALOG_FILES = ("music.db", "tag.db")
_PARTIAL_SUFFIX = ".download"
_CHUNK = 1024 * 1024
SQLITE_HEADER = b"SQLite format 3\x00"


class CatalogDownloadError(Exception):
    """The public catalog couldn't be downloaded or didn't match version.json."""


@dataclass
class CatalogInfo:
    base_url: str
    published_at: str | None
    files: dict  # name -> {"size", "sha256"}

    @property
    def total_bytes(self) -> int:
        return sum(entry["size"] for entry in self.files.values())


def database_dir() -> Path:
    return Path(str(Settings.database_dir))


def missing_databases(directory: Path | None = None) -> list[str]:
    """music.db / tag.db not in the database folder yet."""
    directory = directory or database_dir()
    return [name for name in CATALOG_FILES if not (directory / name).exists()]


def catalog_repo() -> str:
    return Settings.github_db_repo.strip("/") or PUBLIC_CATALOG_DEFAULT_REPO


def catalog_base_url(repo: str | None = None) -> str:
    return f"https://github.com/{repo or catalog_repo()}/releases/download/{PUBLIC_DB_RELEASE_TAG}"


def _get(session, url: str, stream: bool = False):
    try:
        response = session.get(url, timeout=PUBLIC_CATALOG_TIMEOUT, stream=stream)
    except requests.RequestException as exc:
        raise CatalogDownloadError(f"{url}: {exc}") from exc
    if response.status_code != 200:
        response.close()
        hint = " - is the release published there?" if response.status_code == 404 else ""
        raise CatalogDownloadError(f"{url}: HTTP {response.status_code}{hint}")
    return response


def fetch_catalog_info(base_url: str | None = None, session=None) -> CatalogInfo:
    """version.json of the release, checked for what a download needs."""
    base_url = (base_url or catalog_base_url()).rstrip("/")
    session = session or requests.Session()
    response = _get(session, f"{base_url}/{PUBLIC_DB_VERSION_FILE}")
    try:
        document = response.json()
    except ValueError as exc:
        raise CatalogDownloadError(f"{PUBLIC_DB_VERSION_FILE} isn't JSON") from exc
    if not isinstance(document, dict) or not isinstance(document.get("files"), dict):
        raise CatalogDownloadError(f"{PUBLIC_DB_VERSION_FILE} has no file list")
    if document.get("format") != PUBLIC_DB_FORMAT:
        raise CatalogDownloadError(f"the catalog is in format {document.get('format')!r}, this version reads "
                                   f"{PUBLIC_DB_FORMAT} - update MusicDatabase (git pull)")
    files = {}
    for name in CATALOG_FILES:
        entry = document["files"].get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("size"), int) \
                or not isinstance(entry.get("sha256"), str):
            raise CatalogDownloadError(f"{PUBLIC_DB_VERSION_FILE} doesn't describe {name}")
        if not 0 < entry["size"] <= PUBLIC_CATALOG_MAX_BYTES:
            raise CatalogDownloadError(f"{PUBLIC_DB_VERSION_FILE} gives {name} an implausible size")
        files[name] = {"size": entry["size"], "sha256": entry["sha256"].lower()}
    return CatalogInfo(base_url, document.get("published_at"), files)


def _download(session, url: str, target: Path, expected: dict) -> None:
    digest = hashlib.sha256()
    size = 0
    response = _get(session, url, stream=True)
    try:
        with open(target, "wb") as f:
            for chunk in response.iter_content(_CHUNK):
                size += len(chunk)
                if size > expected["size"]:
                    raise CatalogDownloadError(f"{target.name[:-len(_PARTIAL_SUFFIX)]} is bigger than "
                                               f"{PUBLIC_DB_VERSION_FILE} says")
                digest.update(chunk)
                f.write(chunk)
    except requests.RequestException as exc:
        raise CatalogDownloadError(f"{url}: {exc}") from exc
    finally:
        response.close()
    name = target.name[:-len(_PARTIAL_SUFFIX)]
    if size != expected["size"]:
        raise CatalogDownloadError(f"{name}: got {size} bytes, {PUBLIC_DB_VERSION_FILE} says {expected['size']}")
    if digest.hexdigest() != expected["sha256"]:
        raise CatalogDownloadError(f"{name}: SHA-256 doesn't match {PUBLIC_DB_VERSION_FILE}")
    _check_sqlite(target, name)


def _check_sqlite(path: Path, name: str) -> None:
    with open(path, "rb") as f:
        if f.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            raise CatalogDownloadError(f"{name} isn't an SQLite database")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as exc:
        raise CatalogDownloadError(f"{name}: {exc}") from exc
    finally:
        conn.close()
    if result != "ok":
        raise CatalogDownloadError(f"{name} failed SQLite's integrity check: {result}")


def download_public_catalog(directory: Path | None = None, base_url: str | None = None,
                            session=None, info: CatalogInfo | None = None) -> list[Path]:
    """Download the catalog's music.db and tag.db into `directory` (the
    database folder). Never overwrites a database already there. Returns the
    paths written; raises CatalogDownloadError, leaving nothing behind."""
    directory = directory or database_dir()
    session = session or requests.Session()
    try:
        try:
            return _download_catalog(directory, session, info or fetch_catalog_info(base_url, session))
        except (OSError, sqlite3.Error) as exc:
            # A full disk, a folder it can't write to, a file SQLite can't open
            raise CatalogDownloadError(f"{type(exc).__name__}: {exc}") from exc
    except CatalogDownloadError as exc:
        flog(f"Public catalog download failed: {exc}")
        raise


def _download_catalog(directory: Path, session, info: CatalogInfo) -> list[Path]:
    names = missing_databases(directory)
    if not names:
        return []
    directory.mkdir(parents=True, exist_ok=True)
    partial = {name: directory / (name + _PARTIAL_SUFFIX) for name in names}
    try:
        for name in names:
            _download(session, f"{info.base_url}/{name}", partial[name], info.files[name])
        written = []
        for name in names:
            os.replace(partial[name], directory / name)
            written.append(directory / name)
    finally:
        for path in partial.values():
            path.unlink(missing_ok=True)
    mlog(f"Public catalog downloaded from {info.base_url} (published {info.published_at}): {names}")
    return written


def create_empty_databases() -> list[str]:
    """Create whichever of music.db / tag.db is missing, empty, with the
    models' current schema. Returns the names created."""
    names = missing_databases()
    if "music.db" in names:
        from utils.database.music_db_manager import create_music_db
        create_music_db()
    if "tag.db" in names:
        from utils.database.tag_db_manager import create_tag_db
        create_tag_db()
    if names:
        mlog(f"Empty databases created in {database_dir()}: {names}")
    return names
