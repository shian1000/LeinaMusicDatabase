"""A song's YouTube videos other than its own link, in music.db's
additional_youtube_videos (migration 0018): other uploads known to be the
same recording. They only tell which song a video is - Leina Radio looks a
played video up there after songs.youtube_video_id, so a video whose title
names the song differently is still recognised. Never played or searched for.

songs.youtube_video_id wins: a video that is a song's own link is never
another song's additional video, and a song's own link isn't repeated here.
A video belongs to one song (it's the key); the rows follow merges and
deletions through the table's trigger on song_id_changes.

Added from "Create playlist from folder and compare with YT playlist" (the
video paired with a file whose song already had a link; a song without a
link gets the video as its own) and by song merges (the dropped song's
link). Every function works in the session it's given; the caller commits.
"""

from dataclasses import dataclass, field

from config.constants import BAD_VIDEO_REASONS, NO_VIDEO_SENTINEL
from utils.database.datatables import AdditionalYoutubeVideo, BadYoutubeVideo, Song
from utils.database.song_artists import song_artist_label


def _real_video(video_id: str | None) -> bool:
    return bool(video_id) and video_id != NO_VIDEO_SENTINEL


def song_for_video(session, video_id: str | None) -> Song | None:
    """The song this video is: the one whose own link it is, else the one
    it's an additional video of."""
    if not _real_video(video_id):
        return None
    song = session.query(Song).filter(Song.youtube_video_id == video_id).order_by(Song.id).first()
    if song is not None:
        return song
    row = session.get(AdditionalYoutubeVideo, video_id)
    return session.get(Song, row.song_id) if row is not None else None


def additional_video_ids(session, song_id: int) -> list[str]:
    return [v for (v,) in session.query(AdditionalYoutubeVideo.video_id)
            .filter(AdditionalYoutubeVideo.song_id == song_id).order_by(AdditionalYoutubeVideo.video_id)]


def add_additional_video(session, song: Song, video_id: str | None, source: str) -> bool:
    """Make `video_id` an additional video of `song` (a code of
    constants.ADDITIONAL_VIDEO_SOURCES). Not when it's the song's own link,
    or any song's video already. True when added."""
    if not _real_video(video_id) or video_id == song.youtube_video_id:
        return False
    if song_for_video(session, video_id) is not None:
        return False
    session.add(AdditionalYoutubeVideo(video_id=video_id, song_id=song.id, source=source))
    return True


def drop_additional_videos(session, video_ids, song_id: int) -> None:
    """These videos aren't `song_id`'s after all (found wrong for it)."""
    video_ids = [v for v in video_ids if v]
    if video_ids:
        (session.query(AdditionalYoutubeVideo)
         .filter(AdditionalYoutubeVideo.video_id.in_(video_ids), AdditionalYoutubeVideo.song_id == song_id)
         .delete(synchronize_session="fetch"))


@dataclass
class VideoLink:
    """A video to save for a song: its own link (`main`, the song had none)
    or an additional video. `sure` = the pairing is certain (the same song,
    or picked by the owner), as opposed to resembling names."""
    song: Song
    video_id: str
    main: bool
    sure: bool


@dataclass
class VideoLinkPlan:
    links: list = field(default_factory=list)      # VideoLink
    conflicts: list = field(default_factory=list)  # (Song, video id, why), not saved


def plan_video_links(session, pairs) -> VideoLinkPlan:
    """What saving these (song id, video id, sure) pairs would do. Left out
    without a word: a video the song has already. Left out as a conflict: a
    video another song has (own link or additional), one found bad for this
    song or for every song, one paired with two songs here. A song without a
    link gets its first video as its own, the rest as additional; a song
    marked "no video" ("N/A") keeps the mark and gets them as additional."""
    plan = VideoLinkPlan()
    planned: dict[str, Song] = {}
    main_planned: set[int] = set()
    for song_id, video_id, sure in pairs:
        song = session.get(Song, song_id) if song_id is not None else None
        if song is None or not _real_video(video_id):
            continue
        if video_id in planned:
            if planned[video_id].id != song.id:
                plan.conflicts.append((song, video_id, f"paired with {_label(planned[video_id])} too"))
            continue
        if video_id == song.youtube_video_id:
            continue
        owner = song_for_video(session, video_id)
        if owner is not None:
            if owner.id != song.id:
                plan.conflicts.append((song, video_id, f"already a video of {_label(owner)}"))
            continue
        bad = (session.query(BadYoutubeVideo).filter(BadYoutubeVideo.video_id == video_id)
               .filter((BadYoutubeVideo.song_id.is_(None)) | (BadYoutubeVideo.song_id == song.id)).first())
        if bad is not None:
            plan.conflicts.append((song, video_id, f"found bad: {BAD_VIDEO_REASONS.get(bad.reason, bad.reason)}"))
            continue
        main = not song.youtube_video_id and song.id not in main_planned
        if main:
            main_planned.add(song.id)
        planned[video_id] = song
        plan.links.append(VideoLink(song, video_id, main, sure))
    return plan


def apply_video_links(session, links, source: str) -> tuple[int, int]:
    """Save these VideoLinks (`source` for the additional ones). Returns
    (own links set, additional videos added)."""
    main = added = 0
    for link in links:
        if link.main and not link.song.youtube_video_id:
            link.song.youtube_video_id = link.video_id
            main += 1
        elif add_additional_video(session, link.song, link.video_id, source):
            added += 1
        session.flush()
    return main, added


def _label(song: Song) -> str:
    return f"{song_artist_label(song)} - {song.title}"
