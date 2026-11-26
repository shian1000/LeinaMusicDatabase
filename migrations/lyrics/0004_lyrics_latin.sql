-- Lyrics in Latin letters (utils/common/lyrics_latin.py), sent to Leina Radio
-- with GET /lyrics/<song id>. Worked out by lyrics_store.refresh_lyrics_latin()
-- before each copy for the phone, again whenever made_from no longer matches
-- the text, the song's language or its artist's origin.
CREATE TABLE lyrics_latin (
	song_id   INTEGER NOT NULL,        -- music.db songs.id, as lyrics.song_id
	method    TEXT NOT NULL,           -- 'default' (the usual rules) / 'uk-slavic' (Ukrainian's Slavic alphabet)
	plain     TEXT,                    -- lyrics.plain line for line; NULL when it can't be done
	synced    TEXT,                    -- lyrics.synced with the same time stamps; NULL likewise
	source    TEXT NOT NULL,           -- 'romanize' (MusicDatabase's rules)
	made_from TEXT,                    -- a hash of the rules' version, the text, language and origin
	made_at   TEXT NOT NULL,           -- UTC 'YYYY-MM-DD HH:MM:SS'
	PRIMARY KEY (song_id, method)
);
