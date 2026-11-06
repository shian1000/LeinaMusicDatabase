"""songs.project_id and the projects dictionary (migration 0019): the series or
compilation a recording was made for - Tiny Desk, Like a Version, Kaunas
Acoustics. The artist stays the real artist, the song is shown "Project: Artist
- Title". A project's recording and the studio song of the same title are two
songs: song_kinds.songs_for_entry() keeps them apart when matching, the
duplicate search too. Reading a project out of names: utils/common/project_names.py;
the import does it in sanitize's "project_name" step, the songs already stored
are offered in a list (project_proposals(), utils/ui/project_review.py).
"""
import sqlite3
from dataclasses import dataclass

from config.constants import SONG_KIND_FULL_ALBUM
from settings import Settings
from utils.common.debug import slog
from utils.common.project_names import ProjectName, name_key, read_project, split_names
from utils.database.datatables import Artist, Project, Song

_known = {}  # database path -> [ProjectName]


def known_projects() -> list[ProjectName]:
    """The dictionary, for sanitize's step, which has no session to hand: read
    once per process. Empty when music.db can't be read or has no projects
    table yet - names then simply aren't read for projects."""
    path = Settings.database_dir / "music.db"
    if path not in _known:
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                rows = connection.execute("SELECT name, aliases, channels FROM projects ORDER BY id").fetchall()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            slog(f"[Projects] Couldn't read the dictionary: {exc}", priority=1)
            return []
        _known[path] = [ProjectName(name, split_names(aliases), split_names(channels)) for name, aliases, channels in rows]
    return _known[path]


def forget_known_projects() -> None:
    """After a committed change of the dictionary."""
    _known.clear()


def _as_name(project: Project) -> ProjectName:
    return ProjectName(project.name, split_names(project.aliases), split_names(project.channels))


def project_named(session, name: str | None) -> Project | None:
    """The project `name` is the name or another spelling of."""
    key = name_key(name)
    if not key:
        return None
    return next((p for p in session.query(Project).all() if key in map(name_key, _as_name(p).spellings)), None)


def add_project(session, name: str, aliases: str | None = None, channels: str | None = None) -> Project:
    """A new project, or ValueError when the name is another's spelling. The
    caller commits."""
    name = name.strip()
    if not name_key(name):
        raise ValueError("A project needs a name")
    if project_named(session, name) is not None:
        raise ValueError(f"'{name}' is already in the dictionary")
    project = Project(name=name, aliases=aliases or None, channels=channels or None)
    session.add(project)
    session.flush()
    return project


def set_song_project(songs: list[Song], project: Project | None) -> None:
    """The caller commits."""
    for song in songs:
        song.project = project


def delete_project(session, project: Project) -> int:
    """Remove `project` from the dictionary; its songs stay, with no project.
    How many had it. The caller commits."""
    songs = session.query(Song).filter(Song.project_id == project.id).all()
    set_song_project(songs, None)
    session.delete(project)
    return len(songs)


def project_song_counts(session) -> dict:
    """project id -> how many songs it has."""
    counts = {}
    for project_id, in session.query(Song.project_id).filter(Song.project_id.isnot(None)):
        counts[project_id] = counts.get(project_id, 0) + 1
    return counts


@dataclass
class ProjectProposal:
    """A stored song that names a project: what it would become."""
    song: Song
    project: Project
    artist: str
    title: str
    whole: bool = False
    guessed: bool = False
    artist_unknown: bool = False


def project_proposals(session) -> list[ProjectProposal]:
    """Every song with no project whose artist, title or album names one from
    the dictionary, by artist."""
    projects = session.query(Project).all()
    names = [_as_name(p) for p in projects]
    by_name = {p.name: p for p in projects}
    proposals = []
    for song in session.query(Song).filter(Song.project_id.is_(None)).order_by(Song.id):
        reading = read_project(song.artist.name, song.title, names, album=song.album, guess=True)
        if reading:
            proposals.append(ProjectProposal(song, by_name[reading.project], reading.artist, reading.title,
                                             reading.whole, reading.guessed, reading.artist_unknown))
    return sorted(proposals, key=lambda p: (p.project.name.lower(), p.artist.lower(), p.title.lower()))


def apply_project_proposal(session, proposal: ProjectProposal) -> None:
    """Give the song its project, title and artist: credited to the artist of
    that name, never renaming the shared row; an artist left without songs goes.
    A whole set is a full album, its album text dropped when that only named
    the project. The caller commits."""
    from utils.database.artist_reassignment import is_unused, remove_unused_artist, set_primary_artist
    from utils.database.song_kinds import set_song_kind
    from utils.discoveries.import_engine import find_matching_artist

    song = proposal.song
    set_song_project([song], proposal.project)
    song.title = proposal.title
    if proposal.artist != song.artist.name:
        old = song.artist
        artist = find_matching_artist(proposal.artist, session) or Artist(name=proposal.artist)
        if artist.id is None:
            session.add(artist)
            session.flush()
        set_primary_artist(session, song, artist)
        if is_unused(session, old):
            remove_unused_artist(session, old, artist, keep_names=False)
    if proposal.whole:
        set_song_kind([song], SONG_KIND_FULL_ALBUM, file_under_album=False)
        if read_project(None, None, [_as_name(proposal.project)], album=song.album):
            song.album = None
    session.flush()

