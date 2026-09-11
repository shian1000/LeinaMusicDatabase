-- Baseline: schema as of adopting the migration mechanism (2026-09).
-- Documentary only -- see migrations/music/0001_baseline.sql for what that
-- means and why. Matches src/utils/database/create_tag_db.py at the time
-- this file was written.

CREATE TABLE tags (
	id INTEGER NOT NULL,
	name VARCHAR NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE song_tags (
	id INTEGER NOT NULL,
	song_id INTEGER NOT NULL,
	tag_id INTEGER NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uix_song_tag UNIQUE (song_id, tag_id)
);
