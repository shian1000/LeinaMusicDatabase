-- The name a song was asked by (lyrics_store.lyrics_key(): artist and title,
-- normalized): a song renamed since is asked of every module again - its
-- misses were answers about another name. NULL (the rows before this) counts
-- for any name.
ALTER TABLE lyrics_module_misses ADD COLUMN asked_as TEXT;
