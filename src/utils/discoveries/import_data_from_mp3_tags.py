import re
from pathlib import Path

from utils.common.debug import debug_on, mlog
from utils.common import trace
from utils.common.song_entry import SongEntry, describe_cuts
from utils.common.sanitize import SOURCE_FILE_NAME, SOURCE_MP3, sanitize_metadata, sanitize_raw, unescape_entities
from utils.discoveries.mp3_utils import audio_duration, extract_metadata_with_fallback, extract_unknown_data
from utils.discoveries.import_engine import ImportResult, run_import_batch


def _clean_fields(metadata: dict) -> dict:
    """Artist, title and album as characters ("Major Lazer &amp; DJ Snake",
    Polish tags read as Latin-1: "Ma³pa" -> "Małpa")
    and without download-site names ("y2meta.com", "(greenmp3.com)"); a
    field that was nothing but one is empty, so the file name is read -
    sanitize's "mp3" steps."""
    return sanitize_metadata(metadata, SOURCE_MP3)


def read_file_metadata(file: Path) -> SongEntry:
    """One mp3's artist/title/album/year/language/origin: ID3 tags, falling
    back to the filename when the artist or title tag is missing. Shared
    with the cover import (utils/discoveries/mp3_covers.py), so both match a
    file to the same song."""
    tags = extract_metadata_with_fallback(file)
    metadata = _clean_fields(SongEntry(tags, source=SOURCE_MP3, raw_text=file.stem, file=file, originals={
        field: tags.get(key) for field, key in (("artist", "artist_name"), ("title", "title"), ("album", "album"))
        if tags.get(key) is not None}))

    if metadata.artist in {"Unknown Artist", None, ""} or metadata.title in {"Unknown Title", None, ""}:
        print("Warning - title/artist tags missing. Trying to extract data from filename")
        artist, title = extract_unknown_data(file)
        if not artist:
            print("The filename didn't have a proper separator")
            print(metadata)
            print(file)
            if metadata.title in {"Unknown Title", None, ""}:
                # The whole name may be the title ("Svefn-g-englar" by Sigur
                # Rós, "Jay-Z Song" is not split either): read with no artist,
                # LocalSongMatcher then tries it as a title alone
                metadata.title = file.stem.strip() or None
                metadata = _clean_fields(metadata)
        else:
            metadata.artist = artist
            metadata.title = title
            metadata = _clean_fields(metadata)
    stem = sanitize_raw(file.stem, SOURCE_FILE_NAME)
    if metadata.artist and not re.search(r" [–—\-_] ", stem):
        artist, title = extract_unknown_data(file)
        split = _clean_fields({"artist_name": artist, "title": title})
        if (split["artist_name"], split["title"]) == (metadata.artist, metadata.title):
            # Read from a name split on a bare hyphen or a double space (here or
            # in extract_metadata_with_fallback()) - only a guess, the whole name
            # may be the title ("Svefn-g-englar" by Sigur Rós), which
            # LocalSongMatcher tries too
            metadata.unsplit_title = unescape_entities(stem)
    # Tells a whole-album file from a track when matching (song_kinds.songs_for_entry())
    metadata.duration_s = audio_duration(file)
    if debug_on(trace.DETAIL):  # the library scan reads every file
        mlog(f"[Read] {file.name} -> \"{metadata.artist}\" - \"{metadata.title}\"{describe_cuts(metadata)}",
             trace.DETAIL)
    return metadata


def _build_metadata_list(folder_path: str) -> tuple:
    """Read every mp3 under folder_path and extract artist/title/album/year/
    language metadata from ID3 tags (falling back to the filename when tags
    are missing). Returns (metadata_list, pre_skipped) - pre_skipped is a
    list of (label, reason) for files that couldn't be resolved to any
    artist/title at all, to be folded into run_import_batch's skip summary.
    """
    folder = Path(folder_path).resolve()
    if not folder.exists():
        print(f"Folder {folder_path} does not exist.")
        return [], []

    mp3_files = list(folder.rglob("*.mp3"))
    print(f"Found {len(mp3_files)} mp3 files.")

    metadata_list = []
    pre_skipped = []

    for idx, file in enumerate(mp3_files, 1):
        file_name = file.name
        mlog(f"\n[{idx}/{len(mp3_files)}] Extracting metadata: {file_name}")

        metadata = read_file_metadata(file)

        if metadata.artist == "Unknown Artist" or not metadata.artist:
            print("Couldn't establish the artist and title")
            print("##########")
            print()
            pre_skipped.append((f"{metadata.artist} - {metadata.title}", f"Couldn't establish the artist and title (Filename: {file})"))
            continue

        metadata.label = file_name
        metadata_list.append(metadata)

    return metadata_list, pre_skipped


def import_data_from_mp3_tags(folder_path: str) -> ImportResult:
    metadata_list, pre_skipped = _build_metadata_list(folder_path)
    if not metadata_list and not pre_skipped:
        return ImportResult()
    return run_import_batch(metadata_list, pre_skipped)
