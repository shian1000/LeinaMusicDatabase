-- files.db: every audio file in the music library (Settings.local_library_dir)
-- and the music.db song it is. Built by utils/database/files_index.py; a new
-- files.db gets its whole schema from this track (no bootstrap).

-- id is AUTOINCREMENT so a removed file's id is never handed to another file:
-- the phone may keep ids it downloaded earlier.
CREATE TABLE files (
	id           INTEGER PRIMARY KEY AUTOINCREMENT,
	path         TEXT NOT NULL UNIQUE,  -- relative to the library folder, '/' separators
	size         INTEGER NOT NULL,      -- bytes
	mtime        INTEGER NOT NULL,      -- Unix seconds; size + mtime unchanged = file not re-read
	song_id      INTEGER,               -- music.db songs.id; NULL = no song matched
	match_source TEXT NOT NULL DEFAULT 'auto',  -- 'auto' (rematched while NULL) / 'manual' (never touched by a scan)
	artist       TEXT,                  -- from the ID3 tags, else the filename
	title        TEXT,
	duration_s   INTEGER,
	indexed_at   TEXT NOT NULL          -- when the file was last read, UTC 'YYYY-MM-DD HH:MM:SS'
);
CREATE INDEX idx_files_song_id ON files (song_id);

-- How far files.db has replayed music.db's song_id_changes log, like tag.db
-- (migrations/tag/0002) - see utils/database/song_references.py.
CREATE TABLE applied_song_changes (
	id             INTEGER PRIMARY KEY CHECK (id = 1),
	last_change_id INTEGER NOT NULL
);

-- Small key/value state of the scanner, e.g. 'catalog_fingerprint': a hash of
-- what matching reads from music.db (artist names and synonyms, song titles);
-- files left without a song are tried again only when it has changed.
CREATE TABLE index_state (
	key   TEXT PRIMARY KEY,
	value TEXT
);
