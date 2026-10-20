-- Songs Leina Radio asked to have added (the `song_requests` table of a
-- request file) that the owner turned down, published to the MusicDatabaseApp
-- phone client so it doesn't ask for them again (app handoff
-- musicdatabase-song-requests.md). artist / title / youtube_video_id /
-- spotify_track_id are as the request had them (artist NULL = the phone knew
-- none); the phone matches a track on any of them. Written by
-- utils/database/inbox_song_requests.py; a later request matching a row is
-- settled without asking.
CREATE TABLE IF NOT EXISTS rejected_song_requests (
	id               INTEGER PRIMARY KEY,
	artist           TEXT,
	title            TEXT NOT NULL,
	youtube_video_id TEXT,
	spotify_track_id TEXT,
	rejected_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
