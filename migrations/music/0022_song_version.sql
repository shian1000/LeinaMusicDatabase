-- A version of a song: a live recording, a remix, an acoustic take - a row of its
-- own (its own id, video, plays), titled without the marker and shown with it:
-- "Dziwny jest ten świat" with version_kind 'live' is "Dziwny jest ten świat (Live)".
-- version_kind: constants.VERSION_KINDS, NULL = not a version. version_label: the
-- marker as shown ("Maceo Plex Remix"), NULL = the kind's own label. version_of: the
-- original song when music.db has it, else NULL; always the original, never another
-- version. Written by utils/database/versions.py, read by the MusicDatabaseApp phone
-- client (docs/handoffs/song-versions-for-musicdatabaseapp.md).
ALTER TABLE songs ADD COLUMN version_kind TEXT;
ALTER TABLE songs ADD COLUMN version_label TEXT;
ALTER TABLE songs ADD COLUMN version_of INTEGER REFERENCES songs (id);

CREATE INDEX IF NOT EXISTS ix_songs_version_of ON songs (version_of);
