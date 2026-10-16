"""Users -> Enter user database -> "Create playlist from folder": every mp3
under a folder, in alphabetical order of its path within the folder, matched
to the song already in the database the way the tag import matches files
(read_file_metadata() + import_engine.LocalSongMatcher - local only, nothing
created). A file whose tag artist is misspelt only gets a suggestion
(LocalSongMatcher.match_similar()) the user confirms. Nothing is written
here; utils/database/user_playlists.py saves the result once every file has
a song (the user picks one for each file that didn't match), and
mp3_utils.write_song_tags() / rename_to_song() can then copy the database's
data into the files' tags and names."""

import html
from dataclasses import dataclass, field
from pathlib import Path

from mutagen.mp3 import MP3

from config.constants import FILE_BEST_GUESSES_MAX, FILE_TITLE_CANDIDATES_MAX

from utils.common.debug import flog
from utils.database.datatables import Artist, Song
from utils.database.song_artists import song_artist_label
from utils.discoveries.import_data_from_mp3_tags import read_file_metadata
from utils.discoveries.import_engine import LocalSongMatcher, find_matching_artist, find_song_loosely


@dataclass
class FolderTrack:
    file: Path | None    # None: a video of the YouTube playlist with no file
    title: str
    artist: str | None
    song_id: int | None = None
    youtube_video_id: str | None = None
    duration_s: int | None = None
    similar_song: object | None = None  # a Song suggested for an unmatched file
    # Songs to pick from for an unmatched file with no artist whose title
    # several songs have (LocalSongMatcher.title_candidates())
    title_candidates: list = field(default_factory=list)
    # When neither of those: the closest songs by score, (Song, score) pairs
    # (LocalSongMatcher.best_guesses())
    best_guesses: list = field(default_factory=list)
    # song id -> LocalSongMatcher.match_score() of every song suggested above
    scores: dict = field(default_factory=dict)
    picked: bool = False                # its song was confirmed/picked by the user
    # The video of the YouTube playlist it was paired with
    # (folder_youtube_playlist.py), which wins over its song's own link
    youtube_link: str | None = None


@dataclass
class FolderPlaylist:
    folder: Path
    tracks: list = field(default_factory=list)     # FolderTrack, in playlist order
    unmatched: list = field(default_factory=list)  # the FolderTracks with no song in the database
    # Matched without asking though the tag artist is spelt differently
    # (LocalSongMatcher.match_confident()): (FolderTrack, tag artist) pairs
    auto_accepted: list = field(default_factory=list)


def assign_song(track: FolderTrack, song) -> None:
    """Make `track` that database song (a match, or the user's pick for a
    file that didn't match), taking the database's spelling."""
    track.song_id = song.id
    track.title = song.title
    track.artist = song_artist_label(song)
    track.youtube_video_id = track.youtube_link or song.youtube_video_id or None


def song_for_file_plan(session, artist_name: str, title: str) -> tuple:
    """(artist, song) a new song for a file would get: the database's
    artist matching `artist_name` (None = a new one is made), and the song
    of that artist the title already is, as loosely as files are matched
    (None = a new one is made)."""
    artist = find_matching_artist(artist_name)
    if artist is None:
        return None, None
    songs = session.query(Song).filter(Song.artist_id == artist.id).all()
    return artist, find_song_loosely(title, songs)


def add_song_for_file(session, artist_name: str, title: str) -> Song:
    """Add a song by `artist_name` titled `title` for a file the database
    has nothing for - the existing artist (and song) when there is one, as
    song_for_file_plan() shows it. Commits."""
    artist, song = song_for_file_plan(session, artist_name, title)
    if song is not None:
        return song
    song = Song(title=title, artist=artist or Artist(name=artist_name))
    session.add(song)
    session.commit()
    return song


def folder_mp3_files(folder: Path) -> list[Path]:
    """Every mp3 under `folder`, by path within it, case-insensitively."""
    folder = Path(folder)
    return sorted(folder.rglob("*.mp3"), key=lambda f: str(f.relative_to(folder)).casefold())


def audio_duration(file: Path) -> int | None:
    """Length in whole seconds, or None when mutagen can't read it."""
    try:
        return round(MP3(file).info.length)
    except Exception:
        return None


def match_track(playlist: FolderPlaylist, matcher: LocalSongMatcher, track: FolderTrack, metadata: dict,
                min_artist_similarity: float = 1.1, best_guess_min_score: float = 1.1) -> None:
    """Give `track` its database song from `metadata` (artist_name, title)
    the way build_folder_playlist() does, or put it in `playlist.unmatched`
    with a suggestion. `min_artist_similarity`: see
    LocalSongMatcher.match_confident(), `best_guess_min_score`: see
    best_guesses() - the user's settings; the defaults (above 1) never
    auto-accept nor guess."""
    song = matcher.match_exact(metadata)
    confident = None if song else matcher.match_confident(metadata, min_artist_similarity)
    if song is not None:
        assign_song(track, song)
    elif confident is not None:
        # Its tags are wrong, so it's offered the name/tag fix like a pick
        track.picked = True
        assign_song(track, confident)
        playlist.auto_accepted.append((track, metadata.get("artist_name")))
    else:
        track.title, track.artist = metadata["title"], metadata.get("artist_name")
        track.similar_song = matcher.match_similar(metadata)
        if track.similar_song is None:
            track.title_candidates = matcher.title_candidates(metadata, FILE_TITLE_CANDIDATES_MAX)
        if track.similar_song is None and not track.title_candidates:
            track.best_guesses = matcher.best_guesses(metadata, FILE_BEST_GUESSES_MAX, best_guess_min_score)
        suggested = [track.similar_song, *track.title_candidates, *(song for song, _ in track.best_guesses)]
        track.scores = {song.id: matcher.match_score(metadata, song) for song in suggested if song is not None}
        playlist.unmatched.append(track)
        flog(f"Playlist from folder: no song in the database for {track.file or metadata.get('_label')}")


def build_folder_playlist(session, folder: Path, min_artist_similarity: float = 1.1,
                          best_guess_min_score: float = 1.1) -> FolderPlaylist:
    """`min_artist_similarity`: see match_track()."""
    playlist = FolderPlaylist(folder=Path(folder))
    matcher = LocalSongMatcher(session)
    for file in folder_mp3_files(folder):
        try:
            metadata = read_file_metadata(file)
        except ValueError:
            metadata = {}
        if not metadata.get("title"):
            # No title tag and no "Artist - Title" name: the name is the title,
            # as the user is shown it
            metadata = {**metadata, "title": html.unescape(file.stem)}
        track = FolderTrack(file=file, title=metadata["title"], artist=None)
        match_track(playlist, matcher, track, metadata, min_artist_similarity, best_guess_min_score)
        track.duration_s = audio_duration(file)
        playlist.tracks.append(track)
    return playlist
