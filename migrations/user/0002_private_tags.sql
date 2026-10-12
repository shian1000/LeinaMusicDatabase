-- A user's private tags: the same shape as the global tags in tag.db (which
-- every user sees), but only this user's. Living in user.db is what makes
-- them private, so the table names stay the same as tag.db's. A private tag
-- may share its name with a global one. song_id = music.db's songs.id (plain
-- integer, as in tag.db).
CREATE TABLE IF NOT EXISTS tags (
	id   INTEGER PRIMARY KEY,
	name VARCHAR NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS song_tags (
	id      INTEGER PRIMARY KEY,
	song_id INTEGER NOT NULL,
	tag_id  INTEGER NOT NULL REFERENCES tags(id),
	CONSTRAINT uix_song_tag UNIQUE (song_id, tag_id)
);
