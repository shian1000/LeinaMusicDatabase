-- A song's album row (0005). NULL = no album text, or a title several main
-- artists share that the user hasn't settled yet (Manage database -> "Resolve
-- albums with several artists"). Filled for existing songs by
-- albums.link_unassigned_songs() right after this file is applied (main.py).
ALTER TABLE songs ADD COLUMN album_id INTEGER REFERENCES albums(id);

CREATE INDEX IF NOT EXISTS ix_songs_album_id ON songs (album_id);
