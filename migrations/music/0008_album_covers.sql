-- Where an album's cover comes from, one row per (album, source). The image
-- itself is never stored in music.db (the phone downloads the whole file);
-- 'file'/'manual' covers live in <database_dir>/covers/ and `path` is
-- relative to that folder. status 'missing' = the source was checked and has
-- none. Written by utils/discoveries/mp3_covers.py (source 'file').
CREATE TABLE IF NOT EXISTS album_covers (
	album_id   INTEGER NOT NULL REFERENCES albums(id),
	source     VARCHAR NOT NULL CHECK (source IN ('file', 'itunes', 'deezer', 'musicbrainz', 'youtube', 'manual')),
	url        VARCHAR,
	path       VARCHAR,
	width      INTEGER,
	height     INTEGER,
	bytes      INTEGER,
	status     VARCHAR NOT NULL CHECK (status IN ('found', 'missing')),
	checked_at TEXT NOT NULL DEFAULT (datetime('now')),
	PRIMARY KEY (album_id, source)
);
