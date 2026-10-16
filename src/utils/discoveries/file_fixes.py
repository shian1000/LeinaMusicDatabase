"""Manage database -> "Fix names and tags for mp3 files that are in the
database": every mp3 under a folder matched to its song the way "Create
playlist from folder" matches files without asking (folder_playlist.
build_folder_playlist(): exact, a misspelt artist above the auto-accept
setting, a suggestion above the auto-accept score), and for those whose name
or tags differ from the database, "Artist - Title.mp3" and the song's tags
(mp3_utils.rename_to_song() / write_song_tags()). Files it can't match are
only counted - "Create playlist from folder" is where they get a song by
hand."""

from dataclasses import dataclass
from pathlib import Path

from utils.database.datatables import Song
from utils.discoveries.folder_playlist import build_folder_playlist
from utils.discoveries.mp3_utils import rename_to_song, song_file_name, write_song_tags


@dataclass
class FileFix:
    file: Path
    song: Song
    new_name: str | None   # None: the name is already the song's
    tags_differ: bool


@dataclass
class FileFixPlan:
    fixes: list            # FileFix, for the files whose name or tags differ
    up_to_date: int        # matched files already named and tagged like their song
    not_in_database: list  # Paths of the files no song was found for without asking


def plan_file_fixes(session, folder: Path, min_artist_similarity: float = 1.1,
                    auto_accept_score: float = 1.1) -> FileFixPlan:
    """What fixing `folder` would change; nothing is written. The two
    thresholds are the user's settings (see build_folder_playlist()); the
    closest-songs guesses are skipped, as nobody would confirm them here."""
    playlist = build_folder_playlist(session, folder, min_artist_similarity, 1.1, auto_accept_score)
    plan = FileFixPlan(fixes=[], up_to_date=0, not_in_database=[])
    for track in playlist.tracks:
        if track.song_id is None:
            plan.not_in_database.append(track.file)
            continue
        song = session.get(Song, track.song_id)
        new_name = song_file_name(song, track.file.suffix)
        fix = FileFix(file=track.file, song=song, new_name=new_name if new_name != track.file.name else None,
                      tags_differ=write_song_tags(track.file, song, save=False))
        if fix.new_name or fix.tags_differ:
            plan.fixes.append(fix)
        else:
            plan.up_to_date += 1
    return plan


def apply_file_fix(fix: FileFix) -> Path:
    """Write the song's tags and rename the file; returns its new path.
    Raises FileExistsError (tags already written) rather than overwrite
    another file."""
    if fix.tags_differ:
        write_song_tags(fix.file, fix.song)
    return rename_to_song(fix.file, fix.song) if fix.new_name else fix.file
