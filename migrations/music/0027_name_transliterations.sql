-- Latin forms of names in other alphabets, one per transliteration method:
-- "Океан Ельзи" -> uk-national "Okean Elzy", uk-slavic "Okean Eľzy". For
-- artists.name, songs.title and albums.title (row_id = that row's id); only
-- artists are filled so far. method = constants.TRANSLITERATION_METHODS
-- ("uk-national", "uk-slavic", "ru-english", "ja-hepburn", ...); source
-- "romanize" = worked out by utils/common/romanize.py (refreshed before every
-- copy for the phone), "owner" = written by the owner, never overwritten.
-- artists.name_latin / songs.title_latin / albums.title_latin stay the
-- owner's chosen display form; these are more ways of spelling the name, which
-- search and matching count like synonyms. Written by
-- utils/database/transliterations.py; app handoff
-- docs/handoffs/name-transliterations-for-musicdatabaseapp.md.
CREATE TABLE IF NOT EXISTS name_transliterations (
	id          INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,  -- never reused: a trigger deletes rows under the ORM
	table_name  TEXT NOT NULL,
	row_id      INTEGER NOT NULL,
	column_name TEXT NOT NULL,
	method      TEXT NOT NULL,
	value       TEXT NOT NULL,
	source      TEXT NOT NULL,
	UNIQUE (table_name, row_id, column_name, method)
);

-- A deleted row's forms go with it (merges delete the merged-away row; the
-- owner's forms are carried over in Python first). A renamed name's automatic
-- forms are dropped at once, so the old spelling isn't searched for until the
-- next refresh works the new one out; the owner's stay, as name_latin does.
CREATE TRIGGER IF NOT EXISTS name_transliterations_artist_deleted
AFTER DELETE ON artists
BEGIN
	DELETE FROM name_transliterations WHERE table_name = 'artists' AND row_id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS name_transliterations_artist_renamed
AFTER UPDATE OF name ON artists WHEN OLD.name IS NOT NEW.name
BEGIN
	DELETE FROM name_transliterations
		WHERE table_name = 'artists' AND row_id = OLD.id AND column_name = 'name' AND source = 'romanize';
END;

CREATE TRIGGER IF NOT EXISTS name_transliterations_song_deleted
AFTER DELETE ON songs
BEGIN
	DELETE FROM name_transliterations WHERE table_name = 'songs' AND row_id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS name_transliterations_song_renamed
AFTER UPDATE OF title ON songs WHEN OLD.title IS NOT NEW.title
BEGIN
	DELETE FROM name_transliterations
		WHERE table_name = 'songs' AND row_id = OLD.id AND column_name = 'title' AND source = 'romanize';
END;

CREATE TRIGGER IF NOT EXISTS name_transliterations_album_deleted
AFTER DELETE ON albums
BEGIN
	DELETE FROM name_transliterations WHERE table_name = 'albums' AND row_id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS name_transliterations_album_renamed
AFTER UPDATE OF title ON albums WHEN OLD.title IS NOT NEW.title
BEGIN
	DELETE FROM name_transliterations
		WHERE table_name = 'albums' AND row_id = OLD.id AND column_name = 'title' AND source = 'romanize';
END;
