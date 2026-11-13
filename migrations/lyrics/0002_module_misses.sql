-- What each lyrics module didn't find, per song (utils/database/lyrics_store.py),
-- instead of one miss per song: a song is asked again only of the modules
-- that haven't answered for it (a new module asks every song once), and a
-- text the owner turned down is never taken from that module again.
CREATE TABLE lyrics_module_misses (
	song_id    INTEGER NOT NULL,        -- music.db songs.id
	module_id  TEXT NOT NULL,           -- the discovery module's id ("lrclib_fetcher")
	reason     TEXT NOT NULL,           -- 'not_found' (asked again after LYRICS_RETRY_DAYS) / 'rejected' (never)
	checked_at TEXT NOT NULL,           -- UTC 'YYYY-MM-DD HH:MM:SS'
	PRIMARY KEY (song_id, module_id)
);

-- The first runs' per-song misses are dropped, not carried over: many were
-- LRCLIB answering 429 (too many requests), recorded as "not found".
DROP TABLE lyrics_misses;
