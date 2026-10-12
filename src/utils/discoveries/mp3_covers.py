"""Fetch database data -> "Import covers from mp3 tags": album covers from
the images embedded in the user's mp3 files (ID3 APIC frames).

Each file is matched to its song the way the tag import does it
(read_file_metadata() + import_engine.LocalSongMatcher - no MusicBrainz,
nothing created), and votes for that song's album with its
image. Per album one image wins: the one most of its tracks carry (the same
picture re-encoded at another size counts as the same one, by a difference
hash), ties -> the larger. Albums whose tracks carry different pictures are
reported at the end instead of chosen silently.

Non-square images (aspect outside ALBUM_COVER_MIN/MAX_ASPECT) are video
frames that downloaders wrote in - skipped, so another source can fill the
album later. The winner is re-encoded to at most ALBUM_COVER_MAX_SIZE px
JPEG into <database_dir>/covers/<album_id>.jpg with an album_covers row
(source 'file'); re-running overwrites both, never duplicates. Albums with
no image in any file get nothing written (no 'missing' row - the files just
didn't have one). The Folder.jpg / AlbumArt*.jpg files in the music folders
are Windows Media Player leftovers per playlist folder, not album covers,
and are ignored."""

import io
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError
from PIL import Image, UnidentifiedImageError

from config.constants import (
    ALBUM_COVER_JPEG_QUALITY,
    ALBUM_COVER_MAX_ASPECT,
    ALBUM_COVER_MAX_SIZE,
    ALBUM_COVER_MIN_ASPECT,
    ALBUM_COVER_SAME_IMAGE_MAX_DISTANCE,
    ALBUM_COVER_SOURCE_FILE,
    ALBUM_COVER_STATUS_FOUND,
    ID3_PICTURE_TYPE_FRONT_COVER,
)
from utils.common.debug import flog, slog
from utils.database.albums import cover_filename, covers_dir
from utils.database.datatables import Album, AlbumCover
from utils.discoveries.import_data_from_mp3_tags import read_file_metadata
from utils.discoveries.import_engine import LocalSongMatcher


@dataclass
class EmbeddedCover:
    """One file's picture - the image bytes are re-read only for the winner,
    so a whole library's ~300 MB of covers never sits in memory at once."""
    file: Path
    album_id: int
    width: int
    height: int
    size: int
    dhash: int


@dataclass
class CoverChoice:
    winner: EmbeddedCover
    # One entry per distinct picture: (number of tracks, a representative cover)
    pictures: list


@dataclass
class CoverImportReport:
    files: int = 0
    unreadable: int = 0
    no_image: int = 0
    not_square: int = 0
    unmatched: list = field(default_factory=list)   # file names with no song in the DB
    no_album: int = 0                               # matched a song that has no album row
    albums_written: int = 0
    # (Album, CoverChoice) for albums whose tracks carry different pictures
    disagreements: list = field(default_factory=list)


def pick_picture(tags):
    """The APIC frame to use: the front cover, else the largest picture."""
    frames = tags.getall("APIC") if tags is not None else []
    front = [f for f in frames if f.type == ID3_PICTURE_TYPE_FRONT_COVER]
    pool = front or frames
    return max(pool, key=lambda f: len(f.data)) if pool else None


def read_picture(file: Path) -> bytes | None:
    try:
        frame = pick_picture(ID3(file))
    except ID3NoHeaderError:
        return None
    return frame.data if frame is not None else None


def difference_hash(image: Image.Image) -> int:
    """64-bit dHash: survives re-encoding and resizing, so the same cover at
    500 px and 1200 px hashes (nearly) alike."""
    small = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = small.load()
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | (pixels[x, y] > pixels[x + 1, y])
    return bits


def is_square(width: int, height: int) -> bool:
    return height > 0 and ALBUM_COVER_MIN_ASPECT <= width / height <= ALBUM_COVER_MAX_ASPECT


def choose_cover(covers: list[EmbeddedCover]) -> CoverChoice:
    """Group one album's covers into distinct pictures; the picture most
    tracks carry wins (ties -> larger), and its largest copy is used."""
    groups = []
    for cover in covers:
        for group in groups:
            if bin(group[0].dhash ^ cover.dhash).count("1") <= ALBUM_COVER_SAME_IMAGE_MAX_DISTANCE:
                group.append(cover)
                break
        else:
            groups.append([cover])

    def largest(group):
        return max(group, key=lambda c: (c.width * c.height, c.size))

    groups.sort(key=lambda g: (len(g), largest(g).width * largest(g).height), reverse=True)
    return CoverChoice(winner=largest(groups[0]), pictures=[(len(g), largest(g)) for g in groups])


def encode_cover(data: bytes) -> tuple[bytes, int, int]:
    """Re-encode to a JPEG no bigger than ALBUM_COVER_MAX_SIZE square."""
    image = Image.open(io.BytesIO(data))
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")
    image.thumbnail((ALBUM_COVER_MAX_SIZE, ALBUM_COVER_MAX_SIZE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=ALBUM_COVER_JPEG_QUALITY, optimize=True)
    return out.getvalue(), image.width, image.height


def save_file_cover(session, album: Album, data: bytes, directory: Path) -> AlbumCover:
    """Write covers/<album_id>.jpg (atomically) and its album_covers row.
    Caller commits."""
    jpeg, width, height = encode_cover(data)
    directory.mkdir(parents=True, exist_ok=True)
    name = cover_filename(album.id, ALBUM_COVER_SOURCE_FILE)
    tmp = directory / f"{name}.tmp"
    tmp.write_bytes(jpeg)
    os.replace(tmp, directory / name)

    cover = session.get(AlbumCover, (album.id, ALBUM_COVER_SOURCE_FILE))
    if cover is None:
        cover = AlbumCover(album_id=album.id, source=ALBUM_COVER_SOURCE_FILE)
        session.add(cover)
    cover.url = None
    cover.path = name
    cover.width, cover.height, cover.bytes = width, height, len(jpeg)
    cover.status = ALBUM_COVER_STATUS_FOUND
    cover.checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return cover


def collect_covers(session, files: list[Path], report: CoverImportReport, progress=None) -> dict:
    """album_id -> [EmbeddedCover] for every file whose song has an album."""
    matcher = LocalSongMatcher(session)
    by_album = {}
    for idx, file in enumerate(files, 1):
        if progress and idx % 250 == 0:
            progress(idx, len(files))
        try:
            data = read_picture(file)
        except Exception as exc:
            report.unreadable += 1
            slog(f"[COVERS] can't read tags of {file}: {exc}")
            continue
        if not data:
            report.no_image += 1
            continue
        try:
            image = Image.open(io.BytesIO(data))
            width, height = image.size
            if not is_square(width, height):
                report.not_square += 1
                continue
            dhash = difference_hash(image)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            report.unreadable += 1
            slog(f"[COVERS] bad embedded image in {file}: {exc}")
            continue

        try:
            metadata = read_file_metadata(file)
        except ValueError:
            metadata = {}
        song = matcher.match(metadata)
        if song is None:
            report.unmatched.append(file.name)
            flog(f"Cover import: no song in the database for {file}")
            continue
        if song.album_id is None:
            report.no_album += 1
            continue
        by_album.setdefault(song.album_id, []).append(
            EmbeddedCover(file=file, album_id=song.album_id, width=width, height=height,
                          size=len(data), dhash=dhash)
        )
    return by_album


def import_covers_from_mp3(session, folder: Path, directory: Path | None = None,
                           progress=None) -> CoverImportReport:
    """Scan `folder` for mp3s and store one cover per album found in them.
    Commits as it goes (each album's file and row together)."""
    directory = directory or covers_dir()
    files = sorted(Path(folder).rglob("*.mp3"))
    report = CoverImportReport(files=len(files))
    by_album = collect_covers(session, files, report, progress)

    for album_id, covers in by_album.items():
        album = session.get(Album, album_id)
        choice = choose_cover(covers)
        data = read_picture(choice.winner.file)
        if not data:
            continue
        save_file_cover(session, album, data, directory)
        session.commit()
        report.albums_written += 1
        if len(choice.pictures) > 1:
            report.disagreements.append((album, choice))
    return report
