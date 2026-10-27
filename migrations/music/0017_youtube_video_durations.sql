-- How long a YouTube video is, by its id: what the YouTube Data API said once
-- (a video's length never changes). Filled by Manage database -> "Find full
-- albums" (utils/database/video_durations.py), which asks only about videos
-- not here yet. Published to the MusicDatabaseApp phone client, which joins it
-- on songs.youtube_video_id (playlist lengths; app handoff
-- docs/handoffs/video-durations-for-musicdatabaseapp.md). Keyed by video, not
-- song, so a changed link never shows the old video's length. A live or
-- upcoming stream (length 0) and a video YouTube doesn't list aren't stored.
CREATE TABLE IF NOT EXISTS youtube_video_durations (
	video_id   TEXT PRIMARY KEY NOT NULL,
	duration_s INTEGER NOT NULL CHECK (duration_s > 0),
	fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
