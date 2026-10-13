-- A user's own moods ("Nastroje" on the phone), synced with their phones
-- through the sharing server like playlists (utils/database/mood_sync.py;
-- the app's docs/handoffs/musicdatabase-moods.md). A mood is a private tag
-- (migration 0002, unused until now) with kind = 'mood'; its songs are its
-- song_tags rows. No order, a song at most once.
--
-- The table is rebuilt because tags.name was UNIQUE outright: a deleted mood
-- keeps its row as a marker for the phones, and the user must be able to
-- make a new mood with the same name. Names are now unique among live rows
-- only (ux_tags_live_name).
--
-- kind: 'mood', or 'marker' for anything else (as in tag.db).
-- uid, revision, created_at, updated_at, deleted_at, song_ids_checked_to:
-- as for playlists (migration 0004), with the mood_revision counter below.
-- promoted_to / promoted_to_name: set on the marker of a mood the owner
-- moved into a global mood in tag.db (its tags.id and name then).
-- merged_into: the uid of the mood this one was merged into, when it was
-- renamed to the name of another live mood.
CREATE TABLE tags_new (
	id                  INTEGER PRIMARY KEY,
	name                VARCHAR NOT NULL,
	kind                VARCHAR NOT NULL DEFAULT 'marker',
	uid                 VARCHAR,
	revision            INTEGER NOT NULL DEFAULT 0,
	created_at          INTEGER,
	updated_at          INTEGER,
	deleted_at          INTEGER,
	song_ids_checked_to INTEGER,
	promoted_to         INTEGER,
	promoted_to_name    VARCHAR,
	merged_into         VARCHAR
);
INSERT INTO tags_new (id, name) SELECT id, name FROM tags;
DROP TABLE tags;
ALTER TABLE tags_new RENAME TO tags;

CREATE UNIQUE INDEX IF NOT EXISTS ux_tags_live_name ON tags (name) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_tags_uid ON tags (uid);
CREATE INDEX IF NOT EXISTS ix_tags_revision ON tags (revision);

-- The per-user counter behind tags.revision, separate from playlists'. One row.
CREATE TABLE IF NOT EXISTS mood_revision (
	id            INTEGER PRIMARY KEY CHECK (id = 1),
	last_revision INTEGER NOT NULL
);
INSERT INTO mood_revision (id, last_revision) VALUES (1, 0);
