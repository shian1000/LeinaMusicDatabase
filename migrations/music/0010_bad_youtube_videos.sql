-- YouTube videos found bad, published to the MusicDatabaseApp phone client
-- (it reads music.db directly; app handoff musicdatabase-bad-videos.md).
-- song_id NULL = bad for every song (removed, doesn't play here, a replaced
-- dead link); otherwise wrong for that one song. reason is a code from
-- constants.BAD_VIDEO_REASONS. Written by utils/database/inbox_requests.py
-- (mark_bad_videos() / unmark_bad_video()); a request naming one is turned
-- down without asking YouTube.
CREATE TABLE IF NOT EXISTS bad_youtube_videos (
	id        INTEGER PRIMARY KEY,
	video_id  TEXT NOT NULL,
	song_id   INTEGER,
	reason    TEXT NOT NULL,
	marked_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per (video, song); IFNULL so "every song" is unique too
CREATE UNIQUE INDEX IF NOT EXISTS ux_bad_youtube_videos ON bad_youtube_videos (video_id, IFNULL(song_id, 0));
CREATE INDEX IF NOT EXISTS ix_bad_youtube_videos_song_id ON bad_youtube_videos (song_id);

-- song_id stays a current songs.id, so the phone needs no merge logic: a
-- merged song's rows move to the kept song (one it already has wins), a
-- deleted song's rows go. Every merge and deletion is logged in
-- song_id_changes (utils/database/song_references.py).
CREATE TRIGGER IF NOT EXISTS bad_youtube_videos_follow_songs
AFTER INSERT ON song_id_changes
BEGIN
	UPDATE OR IGNORE bad_youtube_videos SET song_id = NEW.new_song_id
		WHERE song_id = NEW.old_song_id AND NEW.new_song_id IS NOT NULL;
	DELETE FROM bad_youtube_videos WHERE song_id = NEW.old_song_id;
END;
