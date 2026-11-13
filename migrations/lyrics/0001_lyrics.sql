-- lyrics.db: songs' lyrics, next to music.db. Built by
-- utils/database/lyrics_store.py; a new lyrics.db gets its whole schema from
-- this track (no bootstrap). The sharing server answers one song's lyrics
-- from its copy (GET /lyrics/<song id>); the file is never downloaded whole
-- and never published (the lyrics' licence doesn't allow it).

CREATE TABLE lyrics (
	song_id      INTEGER PRIMARY KEY,   -- music.db songs.id
	plain        TEXT,                  -- the text, lines separated by '\n'
	synced       TEXT,                  -- LRC ("[01:23.45] line"), NULL when only the plain text is known
	instrumental INTEGER NOT NULL DEFAULT 0,  -- 1 = the song has no words (plain and synced NULL)
	source       TEXT NOT NULL,         -- discovery module id ("lrclib_fetcher"), or "original" (a version's original)
	source_ref   TEXT,                  -- the source's own id of the text, e.g. LRCLIB's
	fetched_at   TEXT NOT NULL          -- UTC 'YYYY-MM-DD HH:MM:SS'
);

-- Songs looked up without a result: 'not_found' is tried again after
-- LYRICS_RETRY_DAYS, 'rejected' (the owner said the text found was wrong) never.
CREATE TABLE lyrics_misses (
	song_id    INTEGER PRIMARY KEY,
	reason     TEXT NOT NULL,
	checked_at TEXT NOT NULL
);

-- How far lyrics.db has replayed music.db's song_id_changes log, like files.db
-- (migrations/files/0001) - see utils/database/song_references.py.
CREATE TABLE applied_song_changes (
	id             INTEGER PRIMARY KEY CHECK (id = 1),
	last_change_id INTEGER NOT NULL
);
