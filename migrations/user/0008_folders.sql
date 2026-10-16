-- Folders of the phone's Biblioteka, holding the user's playlists and moods,
-- synced with their phones through the sharing server like playlists
-- (utils/database/folder_sync.py; the app's
-- docs/handoffs/musicdatabase-folders.md). The unit is a whole folder.
--
-- uid: the id every device uses, 1-64 characters [A-Za-z0-9-] (a UUID, or a
-- short base-36 id of a folder made on a phone before syncing).
-- items: a JSON list in the user's order, stored exactly as a phone sent it
-- and never checked against the playlists/moods it names - e.g.
-- [{"type": "playlist", "uid": "..."}, {"type": "mood", "uid": "..."},
--  {"type": "global_mood", "id": 5}]; unknown types are kept for later app
-- versions.
-- revision, created_at, updated_at, deleted_at: as for playlists (migration
-- 0004), with the folder_revision counter below. A deleted folder keeps its
-- row without a name or items; nothing is archived (it holds only refs).
CREATE TABLE IF NOT EXISTS folders (
	id         INTEGER PRIMARY KEY,
	uid        VARCHAR NOT NULL,
	name       VARCHAR,
	items      TEXT,
	revision   INTEGER NOT NULL DEFAULT 0,
	created_at INTEGER,
	updated_at INTEGER NOT NULL,
	deleted_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_folders_uid ON folders (uid);
CREATE INDEX IF NOT EXISTS ix_folders_revision ON folders (revision);

-- The per-user counter behind folders.revision, separate from playlists' and
-- moods'. One row.
CREATE TABLE IF NOT EXISTS folder_revision (
	id            INTEGER PRIMARY KEY CHECK (id = 1),
	last_revision INTEGER NOT NULL
);
INSERT INTO folder_revision (id, last_revision) VALUES (1, 0);
