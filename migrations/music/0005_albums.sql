-- Albums as rows, so a cover (and later a track list) can belong to an album
-- rather than a song. songs.album (free text) stays and stays filled: the
-- MusicDatabaseApp phone client reads it. songs.album_id (0006) points here.
-- artist_id NULL = various artists (a compilation or soundtrack). type NULL =
-- not known; 'single' = one song filed under the "Singles" sentinel, which
-- gets an album of its own, titled like the song. Ids are stable - the phone
-- keys its cover cache on them - so rows are never renumbered.
-- See utils/database/albums.py and docs/data-model.md.
CREATE TABLE IF NOT EXISTS albums (
	id        INTEGER PRIMARY KEY,
	title     VARCHAR NOT NULL,
	artist_id INTEGER REFERENCES artists(id),
	year      INTEGER,
	type      VARCHAR CHECK (type IN ('album', 'ep', 'single', 'compilation', 'soundtrack')),
	musicbrainz_release_group_id VARCHAR,
	itunes_collection_id         INTEGER,
	deezer_album_id              INTEGER,
	spotify_album_id             VARCHAR
);

CREATE INDEX IF NOT EXISTS ix_albums_artist_id ON albums (artist_id);
