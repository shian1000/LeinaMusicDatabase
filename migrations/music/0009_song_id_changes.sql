-- Every song id that stopped existing: merged into another song (new_song_id
-- = the song kept) or deleted (new_song_id NULL). Other files point at
-- songs.id without SQLite being able to enforce it - tag.db's song_tags, each
-- user's user.db (playlists, private tags), and later the phone - so each of
-- them replays this log up to its own cursor (utils/database/song_references.py).
-- Append-only; ids here are also never handed out again to a new song.
CREATE TABLE IF NOT EXISTS song_id_changes (
	id          INTEGER PRIMARY KEY AUTOINCREMENT,
	old_song_id INTEGER NOT NULL,
	new_song_id INTEGER,
	changed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_song_id_changes_old_song_id ON song_id_changes (old_song_id);
