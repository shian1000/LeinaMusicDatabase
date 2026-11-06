-- Series and compilations a recording was made for (Tiny Desk, Like a Version,
-- Kaunas Acoustics): the artist stays the real artist, the song only says it's
-- that project's recording, shown "Project: Artist - Title". A project's
-- recording and the studio song of the same title are two rows, never one.
-- aliases: other spellings, comma-separated. channels: YouTube channels (also
-- comma-separated) that publish it and aren't its artists, like "NPR Music".
-- Written by utils/database/projects.py. Read by the MusicDatabaseApp phone
-- client (docs/handoffs/projects-for-musicdatabaseapp.md).
CREATE TABLE IF NOT EXISTS projects (
	id       INTEGER PRIMARY KEY NOT NULL,
	name     TEXT NOT NULL UNIQUE,
	aliases  TEXT,
	channels TEXT
);

ALTER TABLE songs ADD COLUMN project_id INTEGER REFERENCES projects (id);

CREATE INDEX IF NOT EXISTS ix_songs_project_id ON songs (project_id);

INSERT INTO projects (name, aliases, channels) VALUES
	('Kaunas Acoustics', NULL, NULL),
	('Tiny Desk', 'NPR Tiny Desk Concert,NPR Music Tiny Desk Concert,NPR Music Tiny Desk,Tiny Desk Concert,Tiny Desk Concerts,Tiny Desk Home Concert', 'NPR Music'),
	('Like a Version', 'triple j Like a Version,tripple j Like a Version', 'triple j');
