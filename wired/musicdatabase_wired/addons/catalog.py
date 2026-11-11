"""The catalog comes from the public GitHub release, with the checks MDHS's first start
makes: version.json describes each file, and only files matching it replace the old
ones. MDW never changes the catalog itself."""

import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from musicdatabase_core.file_tools import sqlite_problem

from ..config import Config

FILES = ("music.db", "tag.db")
VERSION_FILE = "version.json"
# The format this version reads; a newer one means updating MDW first
CATALOG_FORMAT = 1
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_VERSION_BYTES = 64 * 1024
TIMEOUT_SECONDS = 30
CHUNK_BYTES = 64 * 1024
PARTIAL_SUFFIX = ".partial"


class CatalogError(Exception):
    pass


def _base_url(config: Config) -> str:
    return f"https://github.com/{config.catalog_repo}/releases/download/database"


def _fetch_version(base_url: str) -> dict:
    try:
        with urllib.request.urlopen(f"{base_url}/{VERSION_FILE}", timeout=TIMEOUT_SECONDS) as response:
            document = json.loads(response.read(MAX_VERSION_BYTES + 1)[:MAX_VERSION_BYTES])
    except (OSError, ValueError) as exc:
        raise CatalogError(f"{VERSION_FILE}: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("files"), dict):
        raise CatalogError(f"{VERSION_FILE} has no file list")
    if document.get("format") != CATALOG_FORMAT:
        raise CatalogError(f"the catalog is in format {document.get('format')!r}, this version reads "
                           f"{CATALOG_FORMAT} - update MusicDatabaseWired")
    for name in FILES:
        entry = document["files"].get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("size"), int) \
                or not isinstance(entry.get("sha256"), str):
            raise CatalogError(f"{VERSION_FILE} doesn't describe {name}")
        if not 0 < entry["size"] <= MAX_FILE_BYTES:
            raise CatalogError(f"{VERSION_FILE} gives {name} an implausible size")
    return document


def _download(url: str, target: Path, name: str, expected: dict) -> None:
    digest = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response, open(target, "wb") as file:
            while chunk := response.read(CHUNK_BYTES):
                size += len(chunk)
                if size > expected["size"]:
                    raise CatalogError(f"{name} is bigger than {VERSION_FILE} says")
                digest.update(chunk)
                file.write(chunk)
    except OSError as exc:
        raise CatalogError(f"{name}: {exc}") from exc
    if size != expected["size"]:
        raise CatalogError(f"{name}: got {size} bytes, {VERSION_FILE} says {expected['size']}")
    if digest.hexdigest() != expected["sha256"].lower():
        raise CatalogError(f"{name}: SHA-256 doesn't match {VERSION_FILE}")
    _check_sqlite(target, name)


def _check_sqlite(path: Path, name: str) -> None:
    problem = sqlite_problem(path)
    if problem:
        raise CatalogError(name + problem)


def _installed_version(config: Config) -> dict:
    try:
        return json.loads((config.catalog_dir / VERSION_FILE).read_text())
    except (OSError, ValueError):
        return {}


def catalog_state(config: Config) -> str:
    """For the menu."""
    version = _installed_version(config)
    if not (config.catalog_dir / "music.db").exists():
        return "no catalog yet"
    return f"published {version.get('published_at', 'at an unknown time')}"


def update_catalog(config: Config, base_url: str | None = None) -> bool:
    """Downloads the release when it differs from what's here. True when it did.
    Both files arrive and pass their checks before either replaces the old one."""
    base_url = base_url or _base_url(config)
    document = _fetch_version(base_url)
    installed = _installed_version(config).get("files", {})
    wanted = {name: document["files"][name] for name in FILES}
    if all((config.catalog_dir / name).exists() and installed.get(name, {}).get("sha256") == entry["sha256"]
           for name, entry in wanted.items()):
        return False
    config.catalog_dir.mkdir(parents=True, exist_ok=True)
    partial = {name: config.catalog_dir / (name + PARTIAL_SUFFIX) for name in FILES}
    try:
        for name in FILES:
            _download(f"{base_url}/{name}", partial[name], name, wanted[name])
        for name in FILES:
            os.replace(partial[name], config.catalog_dir / name)
        version_tmp = config.catalog_dir / (VERSION_FILE + PARTIAL_SUFFIX)
        version_tmp.write_text(json.dumps(document))
        os.replace(version_tmp, config.catalog_dir / VERSION_FILE)
    finally:
        for path in partial.values():
            path.unlink(missing_ok=True)
    return True
