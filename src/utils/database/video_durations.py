"""YouTube videos' lengths in music.db's youtube_video_durations (migration
0017), by video id: asked from the YouTube Data API once, since a video's
length never changes. "Find full albums" reads them; Leina Radio joins them
on songs.youtube_video_id (playlist lengths).

A live or upcoming stream says 0 - its length is still to come - and a video
YouTube doesn't list (gone, private) has none, so neither is stored and both
are asked about again next time.
"""
from utils.database.datatables import YoutubeVideoDuration
from config.constants import NO_VIDEO_SENTINEL
from utils.youtube.manage_youtube_playlists import video_durations_via_api

_IN_CHUNK = 500  # SQLite's bound-parameter limit


def stored_video_durations(session, video_ids) -> dict:
    """{video id: seconds} of the given videos music.db already knows."""
    ids = list(dict.fromkeys(v for v in video_ids if v and v != NO_VIDEO_SENTINEL))
    found = {}
    for start in range(0, len(ids), _IN_CHUNK):
        rows = (session.query(YoutubeVideoDuration.video_id, YoutubeVideoDuration.duration_s)
                .filter(YoutubeVideoDuration.video_id.in_(ids[start:start + _IN_CHUNK])))
        found.update(dict(rows))
    return found


def store_video_durations(session, durations: dict) -> None:
    """Saves and commits the lengths (0 left out)."""
    for video_id, seconds in durations.items():
        if video_id and seconds and seconds > 0:
            session.merge(YoutubeVideoDuration(video_id=video_id, duration_s=seconds))
    session.commit()


def video_durations(session, video_ids) -> dict | None:
    """{video id: seconds}: music.db's lengths, the YouTube API asked only
    about the rest (and those stored). None when some are unknown and the API
    can't be used or failed - what it gave before failing is still stored."""
    ids = list(dict.fromkeys(v for v in video_ids if v and v != NO_VIDEO_SENTINEL))
    known = stored_video_durations(session, ids)
    missing = [v for v in ids if v not in known]
    if not missing:
        return known
    fetched = video_durations_via_api(missing, on_found=lambda batch: store_video_durations(session, batch))
    if fetched is None:
        return None
    return {**known, **fetched}
