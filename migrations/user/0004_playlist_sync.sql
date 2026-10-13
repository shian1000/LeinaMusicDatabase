-- Syncing a user's playlists with their phones through the sharing server
-- (utils/database/playlist_sync.py; the app's handoff
-- docs/handoffs/musicdatabase-playlists.md). The server is the hub: phones
-- and the menu change user.db, and phones ask for "everything changed since
-- revision N". A change is always a whole playlist with all its tracks.
--
-- uid: the id every device uses (a UUID made by whoever created the
-- playlist); the integer id stays local to this file.
-- revision: the user's playlist_revision counter at its last change, from
-- any writer (phone, menu, a song merge/deletion reaching its tracks).
-- deleted_at: Unix ms; a deleted playlist keeps its row (uid, revision,
-- updated_at) so every phone learns about it, but loses its tracks.
-- source_owner: the author of an imported YouTube/Spotify playlist (owner
-- stays the username).
-- song_ids_checked_to: set when the sharing server stored the tracks, to the
-- last song_id_changes entry of the *shared copy* of music.db it resolved
-- them against (which may lag music.db by a day); the next
-- apply_song_id_changes() replays what lies between that and this file's
-- cursor for this playlist only and sets it back to NULL.
ALTER TABLE playlists ADD COLUMN uid VARCHAR;
ALTER TABLE playlists ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE playlists ADD COLUMN deleted_at INTEGER;
ALTER TABLE playlists ADD COLUMN source_owner VARCHAR;
ALTER TABLE playlists ADD COLUMN song_ids_checked_to INTEGER;

UPDATE playlists SET uid = lower(
	hex(randomblob(4)) || '-' || hex(randomblob(2)) || '-4' || substr(hex(randomblob(2)), 2) || '-'
	|| substr('89ab', 1 + (abs(random()) % 4), 1) || substr(hex(randomblob(2)), 2) || '-'
	|| hex(randomblob(6)))
WHERE uid IS NULL;
UPDATE playlists SET revision = (SELECT COUNT(*) FROM playlists p WHERE p.id <= playlists.id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_playlists_uid ON playlists (uid);
CREATE INDEX IF NOT EXISTS ix_playlists_revision ON playlists (revision);

-- The per-user counter behind playlists.revision. One row.
CREATE TABLE IF NOT EXISTS playlist_revision (
	id            INTEGER PRIMARY KEY CHECK (id = 1),
	last_revision INTEGER NOT NULL
);
INSERT INTO playlist_revision (id, last_revision) SELECT 1, COUNT(*) FROM playlists;

-- spotify_track_id: the Spotify track of a Spotify import.
-- youtube_removed: 1 = the track's video was removed from YouTube, so the
-- phone looks for another one.
ALTER TABLE playlist_tracks ADD COLUMN spotify_track_id VARCHAR;
ALTER TABLE playlist_tracks ADD COLUMN youtube_removed INTEGER NOT NULL DEFAULT 0;
