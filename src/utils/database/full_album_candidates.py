"""Songs that may really be whole albums (songs.kind, utils/database/song_kinds.py),
for Manage database -> "Find full albums" (utils/ui/full_album_review.py).

Signals, each a reason shown to the owner:
- the title says so ("Migration (Full Album)") - nearly always right;
- a library file of the song (files.db) is at least FULL_ALBUM_MIN_DURATION_S;
- its YouTube video is that long (Data API, when a key is set up).
Long recordings are often mixes or live sets, so those are only offered, not
pre-ticked. Songs already marked, or that the owner said aren't albums
(data/full_album_ignore.json), are left out. A song's title equal to its
album's isn't a signal: that's mostly title tracks.
"""
from dataclasses import dataclass, field

from config.constants import FULL_ALBUM_MIN_DURATION_S
from utils.database.datatables import Song
from utils.database.song_kinds import (
    load_full_album_ignore_list,
    strip_full_album_marker,
    title_has_full_album_marker,
)

REASON_TITLE = "title says full album"


@dataclass
class FullAlbumCandidate:
    song: Song
    reasons: list = field(default_factory=list)
    file_duration_s: int | None = None
    video_duration_s: int | None = None

    @property
    def title_says_so(self) -> bool:
        return REASON_TITLE in self.reasons

    @property
    def cleaned_title(self) -> str:
        """The title without its full-album words (the title itself if it has none)."""
        return strip_full_album_marker(self.song.title)


def _minutes(seconds: int) -> str:
    return f"{round(seconds / 60)} min"


def unmarked_video_ids(session) -> list:
    """The YouTube videos of songs not marked as any kind, to ask their length."""
    return [vid for (vid,) in session.query(Song.youtube_video_id).filter(Song.kind.is_(None)) if vid]


def find_full_album_candidates(session, file_durations: dict, video_durations: dict | None) -> list:
    """Candidates, the ones whose title says so first. `file_durations` =
    files_index.long_song_files(); `video_durations` = {video id: seconds}
    (video_durations.video_durations()) or None when YouTube wasn't asked."""
    ignored = load_full_album_ignore_list()
    candidates = []
    for song in session.query(Song).filter(Song.kind.is_(None)).all():
        if song.id in ignored:
            continue
        candidate = FullAlbumCandidate(song)
        if title_has_full_album_marker(song.title):
            candidate.reasons.append(REASON_TITLE)
        if song.id in file_durations:
            candidate.file_duration_s = file_durations[song.id]
            candidate.reasons.append(f"file {_minutes(candidate.file_duration_s)}")
        video_s = (video_durations or {}).get(song.youtube_video_id)
        if video_s and video_s >= FULL_ALBUM_MIN_DURATION_S:
            candidate.video_duration_s = video_s
            candidate.reasons.append(f"YouTube {_minutes(video_s)}")
        if candidate.reasons:
            candidates.append(candidate)
    candidates.sort(key=lambda c: (not c.title_says_so, (c.song.artist.name or "").lower(), c.song.title.lower()))
    return candidates
