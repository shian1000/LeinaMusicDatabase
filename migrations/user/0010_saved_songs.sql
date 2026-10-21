-- Leina Radio's liked songs ("Polubione utwory"), songs saved "Na później"
-- and links saved "Na później", synced with the user's phones through the
-- sharing server like folders (utils/database/saved_sync.py; the app's
-- docs/handoffs/musicdatabase-saved-songs.md). The unit is one item: one
-- song on one list, or one link.
--
-- uid: the id every device uses, 1-64 characters [A-Za-z0-9-] (for a song a
-- hash of its list and the song, so the same song liked on two phones is one
-- item; for a link a UUID).
-- data: a JSON object of every field the phone sent except uid, revision,
-- updated_at, deleted and base_revision - stored verbatim, unknown keys
-- included, never checked against music.db (a song may be a YouTube video or
-- a Spotify track music.db doesn't have) and the URL never fetched.
-- saved_songs.list: data's "list" ('liked', 'later' or anything newer).
-- saved_songs.song_id: data's song.song_id when it's an integer, kept so a
-- song merge can rewrite it (song_references.py); a deleted song's id stays.
-- revision, updated_at, deleted_at: as for folders (migration 0008), each
-- table with its own counter below. A deleted item keeps its row without
-- data; nothing is archived.
CREATE TABLE IF NOT EXISTS saved_songs (
	id         INTEGER PRIMARY KEY,
	uid        VARCHAR NOT NULL,
	list       VARCHAR,
	song_id    INTEGER,
	data       TEXT,
	revision   INTEGER NOT NULL DEFAULT 0,
	updated_at INTEGER NOT NULL,
	deleted_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_saved_songs_uid ON saved_songs (uid);
CREATE INDEX IF NOT EXISTS ix_saved_songs_revision ON saved_songs (revision);
CREATE INDEX IF NOT EXISTS ix_saved_songs_song_id ON saved_songs (song_id);

CREATE TABLE IF NOT EXISTS later_links (
	id         INTEGER PRIMARY KEY,
	uid        VARCHAR NOT NULL,
	data       TEXT,
	revision   INTEGER NOT NULL DEFAULT 0,
	updated_at INTEGER NOT NULL,
	deleted_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_later_links_uid ON later_links (uid);
CREATE INDEX IF NOT EXISTS ix_later_links_revision ON later_links (revision);

-- The per-user counters behind the two tables' revisions, separate from each
-- other and from playlists', moods', folders' and listens'. One row each.
CREATE TABLE IF NOT EXISTS saved_song_revision (
	id            INTEGER PRIMARY KEY CHECK (id = 1),
	last_revision INTEGER NOT NULL
);
INSERT INTO saved_song_revision (id, last_revision) VALUES (1, 0);
CREATE TABLE IF NOT EXISTS later_link_revision (
	id            INTEGER PRIMARY KEY CHECK (id = 1),
	last_revision INTEGER NOT NULL
);
INSERT INTO later_link_revision (id, last_revision) VALUES (1, 0);
