-- Plays of the user's songs on their phones ("listens"), for Leina Radio's
-- listening stats, synced through the sharing server
-- (utils/database/listen_sync.py; the app's
-- docs/handoffs/musicdatabase-listening-stats.md). Only stored and handed
-- back: the stats are computed on the phone.
--
-- uid: the play's id, made on the phone, 1-64 characters [A-Za-z0-9-].
-- A play is only added, or reported again with a longer listened_ms (the
-- phone sends the song still playing, then its final time): the longer one
-- wins, duration_ms is filled in if it was NULL, nothing else changes. No
-- deletions.
-- started_at: Unix ms, the phone's clock. listened_ms / duration_ms: ms.
-- title, artist, album: the names the phone showed. song_id: music.db's
-- songs.id as the phone sent it, never checked or remapped. source: 'phone',
-- 'server', 'youtube' or anything a later app version sends. playlist_*,
-- mood_uid, global_mood_id (tag.db's tags.id), mood_name: where it played
-- from, as sent.
-- extra: a JSON object of every other key the phone sent (later app
-- versions), NULL when there were none.
-- revision: the listen_revision counter below, separate from playlists',
-- moods' and folders'.
CREATE TABLE IF NOT EXISTS listens (
	id                INTEGER PRIMARY KEY,
	uid               VARCHAR NOT NULL,
	started_at        INTEGER NOT NULL,
	listened_ms       INTEGER NOT NULL,
	duration_ms       INTEGER,
	title             VARCHAR NOT NULL,
	artist            VARCHAR,
	album             VARCHAR,
	song_id           INTEGER,
	youtube_video_id  VARCHAR,
	spotify_track_id  VARCHAR,
	source            VARCHAR,
	playlist_uid      VARCHAR,
	playlist_name     VARCHAR,
	mood_uid          VARCHAR,
	global_mood_id    INTEGER,
	mood_name         VARCHAR,
	extra             TEXT,
	revision          INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_listens_uid ON listens (uid);
CREATE INDEX IF NOT EXISTS ix_listens_revision ON listens (revision);
CREATE INDEX IF NOT EXISTS ix_listens_started_at ON listens (started_at);

-- The per-user counter behind listens.revision. One row.
CREATE TABLE IF NOT EXISTS listen_revision (
	id            INTEGER PRIMARY KEY CHECK (id = 1),
	last_revision INTEGER NOT NULL
);
INSERT INTO listen_revision (id, last_revision) VALUES (1, 0);
