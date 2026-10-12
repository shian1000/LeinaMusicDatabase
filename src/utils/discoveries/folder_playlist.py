"""Users -> Enter user database -> "Create playlist from folder": every mp3
under a folder, in alphabetical order of its path within the folder, matched
to the song already in the database the way the tag import matches files
(read_file_metadata() + import_engine.LocalSongMatcher - local only, nothing
created). Nothing is written here; utils/database/user_playlists.py saves
the result once the user has seen what didn't match."""

from dataclasses import dataclass, field
from pathlib import Path

from mutagen.mp3 import MP3

from utils.common.debug import flog
from utils.database.song_artists import song_artist_label
from utils.discoveries.import_data_from_mp3_tags import read_file_metadata
from utils.discoveries.import_engine import LocalSongMatcher


@dataclass
class FolderTrack:
    file: Path
    title: str
    artist: str | None
    song_id: int | None = None
    youtube_video_id: str | None = None
    duration_s: int | None = None


@dataclass
class FolderPlaylist:
    folder: Path
    tracks: list = field(default_factory=list)     # FolderTrack, in playlist order
    unmatched: list = field(default_factory=list)  # the FolderTracks with no song in the database


def folder_mp3_files(folder: Path) -> list[Path]:
    """Every mp3 under `folder`, by path within it, case-insensitively."""
    folder = Path(folder)
    return sorted(folder.rglob("*.mp3"), key=lambda f: str(f.relative_to(folder)).casefold())


def _duration(file: Path) -> int | None:
    try:
        return round(MP3(file).info.length)
    except Exception:
        return None


def build_folder_playlist(session, folder: Path) -> FolderPlaylist:
    playlist = FolderPlaylist(folder=Path(folder))
    matcher = LocalSongMatcher(session)
    for file in folder_mp3_files(folder):
        try:
            metadata = read_file_metadata(file)
        except ValueError:
            metadata = {}
        song = matcher.match(metadata)
        if song is not None:
            track = FolderTrack(file=file, title=song.title, artist=song_artist_label(song),
                                song_id=song.id, youtube_video_id=song.youtube_video_id or None)
        else:
            track = FolderTrack(file=file, title=metadata.get("title") or file.stem,
                                artist=metadata.get("artist_name"))
            playlist.unmatched.append(track)
            flog(f"Playlist from folder: no song in the database for {file}")
        track.duration_s = _duration(file)
        playlist.tracks.append(track)
    return playlist
