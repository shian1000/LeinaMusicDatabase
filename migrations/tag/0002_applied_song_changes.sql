-- How far tag.db has replayed music.db's song_id_changes log (merged songs'
-- tags moved to the kept song, deleted songs' tags dropped). One row; no row
-- yet = never, see utils/database/song_references.py.
CREATE TABLE IF NOT EXISTS applied_song_changes (
	id             INTEGER PRIMARY KEY CHECK (id = 1),
	last_change_id INTEGER NOT NULL
);
