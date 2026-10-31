-- More YouTube videos of a song than its own link (songs.youtube_video_id):
-- other uploads known to be the same recording, e.g. a YouTube playlist's
-- video paired with a file of a song that already had a link. Only for
-- recognising a video as the song - the phone client (it reads music.db
-- directly; app handoff docs/handoffs/additional-videos-for-musicdatabaseapp.md)
-- looks a played video up here after songs.youtube_video_id, which wins.
-- Never played or searched for. Keyed by video, so a video is one song's.
-- source says what added it (constants.ADDITIONAL_VIDEO_SOURCES). Written by
-- utils/database/additional_videos.py.
CREATE TABLE IF NOT EXISTS additional_youtube_videos (
	video_id TEXT PRIMARY KEY NOT NULL,
	song_id  INTEGER NOT NULL,
	source   TEXT NOT NULL,
	added_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_additional_youtube_videos_song_id ON additional_youtube_videos (song_id);

-- song_id stays a current songs.id, as in bad_youtube_videos: a merged song's
-- videos move to the kept song, a deleted song's go.
CREATE TRIGGER IF NOT EXISTS additional_youtube_videos_follow_songs
AFTER INSERT ON song_id_changes
BEGIN
	UPDATE additional_youtube_videos SET song_id = NEW.new_song_id
		WHERE song_id = NEW.old_song_id AND NEW.new_song_id IS NOT NULL;
	DELETE FROM additional_youtube_videos WHERE song_id = NEW.old_song_id;
END;
