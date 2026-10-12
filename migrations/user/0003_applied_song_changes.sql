-- How far this user.db has replayed music.db's song_id_changes log: a merged
-- song's playlist tracks and private tags follow the kept song, a deleted
-- song's playlist tracks keep their title/artist/video with song_id NULL and
-- its private tags go. One row; see utils/database/song_references.py.
CREATE TABLE IF NOT EXISTS applied_song_changes (
	id             INTEGER PRIMARY KEY CHECK (id = 1),
	last_change_id INTEGER NOT NULL
);
