"""
Cover pictures of a user's playlists and moods, picked on the phone and
synced through the sharing server (docs/runbooks/database.md -> "Covers on
the phone"; the app's docs/handoffs/musicdatabase-covers.md).

The picture travels separately from the playlist/mood JSON, which carries
only its hash: each picture is a content-addressed blob,
Users/<name>/covers/<sha256>.png|.jpg, named by the lowercase hex SHA-256 of
its exact bytes. The rows that use one (playlists.cover_sha256,
tags.cover_sha256, global_mood_covers.cover_sha256 - migration 0006) are the
references:

- A deleted playlist or mood loses its reference; its blob is copied to
  Users/<name>/archive/covers/ first, next to the archived JSON.
- clean_up() removes a blob nothing refers to once its file is at least
  CLEAN_UP_GRACE_SECONDS old (mtime): the phone uploads a blob *before* the
  PUT naming it, so a fresh one is never unreferenced garbage yet. Storing a
  blob that's already there touches its mtime for the same reason.

Standard-library only and free of the app's config: the sharing server
imports it (as do playlist_sync.py and mood_sync.py).
"""

import hashlib
import os
import re
import shutil
import time
from pathlib import Path

# Must match migrations/user/ - the newest migration the sync code reads
REQUIRED_MIGRATION = "0006_covers.sql"
COVERS_SUBDIR = Path("covers")
ARCHIVE_COVERS_SUBDIR = Path("archive") / "covers"
MAX_COVER_BYTES = 4 * 1024 * 1024
CLEAN_UP_GRACE_SECONDS = 24 * 60 * 60
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Content-Type -> (file extension, the signature the bytes must start with)
IMAGE_TYPES = {
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
}
_PART_PREFIX = "."


class CoverError(ValueError):
    """A blob that can't be stored."""


class HashMismatchError(CoverError):
    """The bytes don't hash to the name they were sent under (-> 400)."""


class UnsupportedImageError(CoverError):
    """Not image/png or image/jpeg, or the bytes aren't one (-> 415)."""


class CoverMissingError(CoverError):
    """A playlist/mood/global-mood cover naming a hash the user has no blob
    for (-> 400 {"error": "cover_missing", "cover": <sha>}): the phone
    uploads it again and retries."""

    def __init__(self, sha: str):
        super().__init__(f"cover_missing: {sha}")
        self.cover = sha


def is_sha256(value) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def content_type_of(path: Path) -> str:
    """The content type of a stored blob; UnsupportedImageError for any other
    extension (a stray file in covers/)."""
    content_type = next((t for t, (ext, _) in IMAGE_TYPES.items() if ext == path.suffix), None)
    if content_type is None:
        raise UnsupportedImageError(f"{path.name}: not a stored cover")
    return content_type


def blob_path(user_folder, sha: str) -> Path | None:
    """The stored blob of this hash, or None - also for anything that isn't a
    hash, so a value from a request never becomes a path outside covers/."""
    if not is_sha256(sha):
        return None
    for ext, _ in IMAGE_TYPES.values():
        path = Path(user_folder) / COVERS_SUBDIR / f"{sha}{ext}"
        if path.is_file():
            return path
    return None


def has_blob(user_folder, sha: str) -> bool:
    return blob_path(user_folder, sha) is not None


def require_blob(user_folder, sha: str | None) -> None:
    """Raises CoverMissingError unless `sha` is None or stored."""
    if sha is not None and not has_blob(user_folder, sha):
        raise CoverMissingError(sha)


def store_blob(user_folder, sha: str, data: bytes, content_type: str) -> bool:
    """Store one picture under its hash. Returns True if it was new, False
    if it was already there (its mtime is touched, restarting the clean-up's
    grace period). Raises HashMismatchError / UnsupportedImageError; only
    the signature is checked, the image is never decoded."""
    kind = IMAGE_TYPES.get((content_type or "").split(";", 1)[0].strip().lower())
    if kind is None:
        raise UnsupportedImageError("expected image/png or image/jpeg")
    ext, signature = kind
    if not data.startswith(signature):
        raise UnsupportedImageError(f"the bytes aren't {content_type}")
    if hashlib.sha256(data).hexdigest() != sha:
        raise HashMismatchError("the bytes don't match the sha256 in the path")
    existing = blob_path(user_folder, sha)
    if existing is not None:
        os.utime(existing)
        return False
    folder = Path(user_folder) / COVERS_SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{sha}{ext}"
    part = folder / f"{_PART_PREFIX}{sha}{ext}.part"
    try:
        with open(part, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(part, target)
    finally:
        if part.exists():
            part.unlink()
    return True


def archive_blob(user_folder, sha: str | None) -> Path | None:
    """Copy a deleted playlist's or mood's blob to archive/covers/ (kept
    forever, like the archived JSON). Returns the copy, or None when there's
    no cover or its blob is already gone."""
    if not sha:
        return None
    source = blob_path(user_folder, sha)
    if source is None:
        return None
    folder = Path(user_folder) / ARCHIVE_COVERS_SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / source.name
    if not target.exists():
        shutil.copy2(source, target)
    return target


def referenced(conn) -> set[str]:
    """Every hash a row of this user.db refers to."""
    rows = conn.execute(
        "SELECT cover_sha256 FROM playlists WHERE cover_sha256 IS NOT NULL "
        "UNION SELECT cover_sha256 FROM tags WHERE cover_sha256 IS NOT NULL "
        "UNION SELECT cover_sha256 FROM global_mood_covers WHERE cover_sha256 IS NOT NULL")
    return {r[0] for r in rows}


def clean_up(conn, user_folder, grace_seconds: int = CLEAN_UP_GRACE_SECONDS, now: float | None = None) -> list[Path]:
    """Remove the blobs nothing refers to whose file is older than the grace
    period, and leftovers of interrupted uploads. Returns what was removed."""
    folder = Path(user_folder) / COVERS_SUBDIR
    if not folder.is_dir():
        return []
    keep = referenced(conn)
    now = time.time() if now is None else now
    removed = []
    for path in folder.iterdir():
        if not path.is_file():
            continue
        sha = path.name.lstrip(_PART_PREFIX).split(".", 1)[0]
        if sha in keep and not path.name.startswith(_PART_PREFIX):
            continue
        try:
            if now - path.stat().st_mtime < grace_seconds:
                continue
            path.unlink()
        except FileNotFoundError:
            continue
        removed.append(path)
    return removed
