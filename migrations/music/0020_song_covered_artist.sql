-- The artist a song is a cover of: Hannah Rue's "Crazy in Love" points at
-- Beyoncé and is shown "Hannah Rue - Crazy in Love (Beyoncé cover)". NULL = not
-- known to be a cover. The title no longer carries the credit. Written by
-- utils/database/covers.py. Read by the MusicDatabaseApp phone client
-- (docs/handoffs/cover-songs-for-musicdatabaseapp.md).
ALTER TABLE songs ADD COLUMN covered_artist_id INTEGER REFERENCES artists (id);

CREATE INDEX IF NOT EXISTS ix_songs_covered_artist_id ON songs (covered_artist_id);
