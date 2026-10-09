-- Extra artists for songs credited to more than one artist. The primary
-- artist stays in songs.artist_id; only multi-artist songs get rows here, one
-- per additional artist. role: 'main' = co-headliner ("A x B"), 'feat' =
-- guest ("A feat. B"). See ADDITIONAL_ARTIST_ROLES in constants.py.
--
-- IF NOT EXISTS: Base.metadata.create_all() (music_db_manager) can create this
-- table from the model before the migration runner gets to it.
CREATE TABLE IF NOT EXISTS additional_song_artists (
	song_id   INTEGER NOT NULL REFERENCES songs(id),
	artist_id INTEGER NOT NULL REFERENCES artists(id),
	role      VARCHAR NOT NULL CHECK (role IN ('main', 'feat')),
	PRIMARY KEY (song_id, artist_id)
);

CREATE INDEX IF NOT EXISTS ix_additional_song_artists_artist_id
	ON additional_song_artists (artist_id);
