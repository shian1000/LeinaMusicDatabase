from pathlib import Path

from utils.common.debug import mlog
from utils.discoveries.mp3_utils import extract_metadata_with_fallback, extract_unknown_data
from utils.discoveries.import_engine import run_import_batch
from rich import print


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

        metadata = extract_metadata_with_fallback(file)

        if metadata["artist_name"] in {"Unknown Artist", None, ""} or metadata["title"] in {"Unknown Title", None, ""}:
            print("Warning - title/artist tags missing. Trying to extract data from filename")
            artist, title = extract_unknown_data(file)
            if not artist:
                print("The filename didn't have a proper separator")
                print(metadata)
                print(file)
            else:
                metadata["artist_name"] = artist
                metadata["title"] = title

        if metadata["artist_name"] == "Unknown Artist" or not metadata["artist_name"]:
            print("Couldn't establish the artist and title")
            print("##########")
            print()
            pre_skipped.append((f"{metadata['artist_name']} - {metadata['title']}", f"Couldn't establish the artist and title (Filename: {file})"))
            continue

        metadata["_label"] = file_name
        metadata_list.append(metadata)

    return metadata_list, pre_skipped


def import_data_from_mp3_tags(folder_path: str, mode: str = "skip") -> list:
    metadata_list, pre_skipped = _build_metadata_list(folder_path)
    if not metadata_list and not pre_skipped:
        return []
    return run_import_batch(metadata_list, pre_skipped)
