-- A user's own playlists, in their private Users/<name>/user.db (one file per
-- user - see utils/database/users.py and docs/decisions/0004-per-user-data.md).
-- Same shape as the MusicDatabaseApp phone client's local playlists, so they
-- can be sent here unchanged later. Times are Unix milliseconds, like there.
-- source/source_id: where it was imported from ('youtube' + playlist id).
CREATE TABLE IF NOT EXISTS playlists (
	id         INTEGER PRIMARY KEY,
	name       VARCHAR NOT NULL,
	owner      VARCHAR,
	source     VARCHAR,
	source_id  VARCHAR,
	created_at INTEGER NOT NULL,
	updated_at INTEGER NOT NULL
);

-- Order = position (0-based); the same song may appear twice. song_id points
-- at music.db's songs.id - a plain integer, SQLite can't enforce it across
-- files - and is NULL for a track MusicDatabase doesn't have (a YouTube-only
-- video). title/artist/youtube_video_id are kept either way, so the track can
-- still be shown and played if its song is gone.
CREATE TABLE IF NOT EXISTS playlist_tracks (
	playlist_id      INTEGER NOT NULL REFERENCES playlists(id),
	position         INTEGER NOT NULL,
	song_id          INTEGER,
	title            VARCHAR NOT NULL,
	artist           VARCHAR,
	youtube_video_id VARCHAR,
	duration_s       INTEGER,
	PRIMARY KEY (playlist_id, position)
);

CREATE INDEX IF NOT EXISTS ix_playlist_tracks_song_id ON playlist_tracks (song_id);
