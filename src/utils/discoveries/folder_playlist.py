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

from dataclasses import dataclass, field
from pathlib import Path


from config.constants import FILE_BEST_GUESSES_MAX, FILE_TITLE_CANDIDATES_MAX

from utils.common.debug import flog
from utils.common.sanitize import SOURCE_MP3, unescape_entities
from utils.common.song_entry import SongEntry, as_entry
from utils.database.datatables import Artist, Song
from utils.database.song_artists import song_artist_label
from utils.discoveries.import_data_from_mp3_tags import read_file_metadata
from utils.discoveries.mp3_utils import audio_duration
from utils.database.database_management import add_db_entry
from utils.discoveries.import_engine import LocalSongMatcher, create_song_entry, is_distinctive_title, find_matching_artist, find_song_loosely, song_year


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
    # For saving that video on the song (utils/database/additional_videos.py):
    # the song the video itself matched, and whether the pairing was certain
    # (the same song, or picked by hand) rather than resembling names
    link_song_id: int | None = None
    link_sure: bool = False
    # `artist` is the paired video's: the file itself gave none
    artist_from_video: bool = False
    # The featured artists the cleanup moved out of `title`, credited if the
    # song is added for it (add_song_for_file())
    feat_artists: list = field(default_factory=list)
    # The project the file's name says it's from, likewise; whether it's a whole set
    project: str | None = None
    project_whole: bool = False
    # The same artist's song of this title with no project, to ask about
    # (LocalSongMatcher.without_project())
    project_twin: object | None = None
    # The file's tags, saved if the song is added for it (add_song_for_file())
    album: str | None = None
    year: int | None = None


@dataclass
class FolderPlaylist:
    folder: Path
    tracks: list = field(default_factory=list)     # FolderTrack, in playlist order
    unmatched: list = field(default_factory=list)  # the FolderTracks with no song in the database
    # Matched without asking though the tag artist is spelt differently
    # (LocalSongMatcher.match_confident()): (FolderTrack, tag artist) pairs
    auto_accepted: list = field(default_factory=list)
    # Suggestions taken without asking for their score (the "Closest songs:
    # auto-accept score" setting): (FolderTrack, (tag artist, tag title), score)
    auto_accepted_scored: list = field(default_factory=list)


def assign_song(track: FolderTrack, song) -> None:
    """Make `track` that database song (a match, or the user's pick for a
    file that didn't match), taking the database's spelling."""
    track.song_id = song.id
    track.title = song.title
    track.artist = song_artist_label(song)
    track.youtube_video_id = track.youtube_link or song.youtube_video_id or None


def song_for_file_plan(session, artist_name: str, title: str, project: str | None = None) -> tuple:
    """(artist, song) a new song for a file would get: the database's
    artist matching `artist_name` (None = a new one is made), and the song
    of that artist the title already is, as loosely as files are matched
    (None = a new one is made)."""
    artist = find_matching_artist(artist_name, session)
    if artist is None:
        return None, None
    songs = session.query(Song).filter(Song.artist_id == artist.id).all()
    return artist, find_song_loosely(title, songs, project=project)


def add_song_for_file(session, artist_name: str, title: str, even_if_similar: bool = False,
                      feat_artists: list | None = None, album: str | None = None, year: int | None = None,
                      project: str | None = None, project_whole: bool = False) -> Song:
    """Add a song by `artist_name` titled `title` for a file the database
    has nothing for - the existing artist (and song) when there is one, as
    song_for_file_plan() shows it; with `even_if_similar` a new song even
    then (the user said the one found isn't it). Made like an import's
    song (create_song_entry(): "X (Full Album)" is a full album, and a new
    song is credited `feat_artists` and gets `album` and `year`; an existing
    one is left as it is; `project`: that project's recording). The caller commits."""
    artist, song = song_for_file_plan(session, artist_name, title, project)
    if song is not None and not even_if_similar:
        return song
    if artist is None:
        artist = add_db_entry(Artist(name=artist_name), session)
    return create_song_entry({"title": title, "feat_artists": feat_artists, "album": album, "year": year,
                              "project": project, "project_whole": project_whole}, artist, session)


def folder_mp3_files(folder: Path) -> list[Path]:
    """Every mp3 under `folder`, by path within it, case-insensitively."""
    folder = Path(folder)
    return sorted(folder.rglob("*.mp3"), key=lambda f: str(f.relative_to(folder)).casefold())


def song_suggestions(matcher: LocalSongMatcher, metadata: dict, best_guess_min_score: float = 1.1) -> tuple:
    """What to suggest for tags with no exact song, the first kind that finds
    any: (the similar song, the songs with its title, the closest songs with
    their scores)."""
    similar = matcher.match_similar(metadata)
    candidates = [] if similar else matcher.title_candidates(metadata, FILE_TITLE_CANDIDATES_MAX)
    guesses = [] if similar or candidates else matcher.best_guesses(metadata, FILE_BEST_GUESSES_MAX, best_guess_min_score)
    return similar, candidates, guesses


def match_track(playlist: FolderPlaylist, matcher: LocalSongMatcher, track: FolderTrack, metadata: dict,
                min_artist_similarity: float = 1.1, best_guess_min_score: float = 1.1,
                auto_accept_score: float = 1.1) -> None:
    """Give `track` its database song from `metadata` (artist_name, title)
    the way build_folder_playlist() does, or put it in `playlist.unmatched`
    with a suggestion. `min_artist_similarity`: see
    LocalSongMatcher.match_confident(), `best_guess_min_score`: see
    best_guesses(), `auto_accept_score`: the score from which the one
    suggestion reaching it is taken without asking - the user's settings;
    the defaults (above 1) never auto-accept nor guess."""
    metadata = as_entry(metadata)
    song = matcher.match_exact(metadata)
    confident = None if song else matcher.match_confident(metadata, min_artist_similarity)
    if song is not None:
        assign_song(track, song)
    elif confident is not None:
        # Its tags are wrong, so it's offered the name/tag fix like a pick
        track.picked = True
        assign_song(track, confident)
        playlist.auto_accepted.append((track, metadata.artist))
    else:
        track.title, track.artist = metadata.title, metadata.artist
        track.project_twin = matcher.without_project(metadata)
        track.similar_song, track.title_candidates, track.best_guesses = song_suggestions(
            matcher, metadata, best_guess_min_score)
        suggested = [track.similar_song, *track.title_candidates, *(song for song, _ in track.best_guesses)]
        track.scores = {song.id: matcher.match_score(metadata, song) for song in suggested if song is not None}
        # A file with no artist in the tags is scored on its title alone (the
        # one "Intro" in the database would be 100%): only the one song with
        # its title (match_similar()), and only for a distinctive title -
        # unless its title names a known artist ("X By Y", by_artist())
        read_as = (track.artist, track.title)
        by = None if track.artist else matcher.by_artist(track.title)
        if by:
            title, artist = by
            read_as = (artist, title)
            by_reading = metadata.copy_with(artist=artist, title=title)
            candidates = [song for song in suggested if song is not None
                          and matcher.match_score(by_reading, song, joined=False) == track.scores[song.id]]
            if track.similar_song is not None and is_distinctive_title(track.title):
                candidates.append(track.similar_song)
        elif track.artist:
            candidates = suggested
            unsplit = metadata.unsplit_title
            if unsplit and not is_distinctive_title(unsplit):
                # Scored on the whole file name as a title alone: like a file with no artist
                plain = metadata.copy_with(unsplit_title=None)
                candidates = [song for song in suggested if song is not None
                              and matcher.match_score(plain, song) == track.scores[song.id]]
            elif matcher.joined_title(metadata):
                # Never on the artist and title read as one (joined_title())
                candidates = [song for song in suggested if song is not None
                              and matcher.match_score(metadata, song, joined=False) == track.scores[song.id]]
        elif track.similar_song is not None and is_distinctive_title(track.title):
            candidates = [track.similar_song]
        else:
            candidates = []
        sure = {song.id: song for song in candidates
                if song is not None and track.scores[song.id] >= auto_accept_score
                and matcher.match_score(metadata, song, credits=False) == track.scores[song.id]}
        if len(sure) == 1:
            (song,) = sure.values()
            track.picked = True  # its tags differ from the song's: offered the name/tag fix
            assign_song(track, song)
            playlist.auto_accepted_scored.append((track, read_as, track.scores[song.id]))
            return
        playlist.unmatched.append(track)
        flog(f"Playlist from folder: no song in the database for {track.file or metadata.label}")


def build_folder_playlist(session, folder: Path, min_artist_similarity: float = 1.1,
                          best_guess_min_score: float = 1.1, auto_accept_score: float = 1.1) -> FolderPlaylist:
    """`min_artist_similarity`: see match_track()."""
    playlist = FolderPlaylist(folder=Path(folder))
    matcher = LocalSongMatcher(session)
    for file in folder_mp3_files(folder):
        try:
            metadata = as_entry(read_file_metadata(file))
        except ValueError:
            metadata = SongEntry(source=SOURCE_MP3, file=file, raw_text=file.stem)
        if not metadata.title:
            # No title tag and no "Artist - Title" name: the name is the title,
            # as the user is shown it
            metadata = metadata.copy_with(title=unescape_entities(file.stem))
        track = FolderTrack(file=file, title=metadata.title, artist=None, feat_artists=list(metadata.feat_artists or []),
                            album=metadata.album, year=song_year(metadata.year), project=metadata.project,
                            project_whole=bool(metadata.project_whole))
        track.duration_s = metadata.duration_s or audio_duration(file)
        # The length tells a whole-album file from a track (song_kinds.songs_for_entry())
        metadata = metadata.copy_with(duration_s=track.duration_s)
        match_track(playlist, matcher, track, metadata, min_artist_similarity, best_guess_min_score,
                    auto_accept_score)
        playlist.tracks.append(track)
    return playlist
