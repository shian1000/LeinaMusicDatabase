-- Covers of a user's playlists and moods, picked on the phone and synced
-- through the sharing server (utils/database/covers.py; the app's
-- docs/handoffs/musicdatabase-covers.md). The pictures themselves are files,
-- Users/<name>/covers/<sha256>.png|.jpg - never in this file. A row holds
-- only the lowercase hex SHA-256 of its picture; NULL = no cover. A deleted
-- playlist or mood (a marker row) has none.
ALTER TABLE playlists ADD COLUMN cover_sha256 VARCHAR;
ALTER TABLE tags ADD COLUMN cover_sha256 VARCHAR;

-- The user's private covers of global moods (tag.db's tags.id, which may
-- not or no longer exist there - harmless). Synced like moods, with their
-- own revision counter below; removing a cover keeps the row with NULL so
-- the other phones hear about it.
CREATE TABLE IF NOT EXISTS global_mood_covers (
	mood_id      INTEGER PRIMARY KEY,
	cover_sha256 VARCHAR,
	revision     INTEGER NOT NULL,
	updated_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_global_mood_covers_revision ON global_mood_covers (revision);

-- The per-user counter behind global_mood_covers.revision. One row.
CREATE TABLE IF NOT EXISTS global_mood_cover_revision (
	id            INTEGER PRIMARY KEY CHECK (id = 1),
	last_revision INTEGER NOT NULL
);
INSERT INTO global_mood_cover_revision (id, last_revision) VALUES (1, 0);
