-- Films, games, anime and series a song may be credited to instead of who
-- sings it: YouTube uploads and phone files credit "Nana" for Anna Tsuchiya's
-- "Kuroi Namida", and the name has no "OST" saying so. aliases: other
-- spellings, comma-separated. A work the catalog has an "<work> OST" album of
-- is known without a row here. Written by utils/database/soundtrack_albums.py
-- (Manage database -> "Soundtrack works"); the matchers find a song credited to
-- a work among the songs of any artist whose album names it.
CREATE TABLE IF NOT EXISTS soundtrack_works (
	id      INTEGER PRIMARY KEY NOT NULL,
	name    TEXT NOT NULL UNIQUE,
	aliases TEXT
);

INSERT OR IGNORE INTO soundtrack_works (name) VALUES ('Nana');
