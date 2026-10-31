-- Tracks the user added by hand to an imported (YouTube / Spotify) playlist,
-- which a phone keeps, each after the source track it followed, when it
-- imports the playlist again (playlist_sync.py; the app's
-- docs/handoffs/musicdatabase-added-tracks.md). 1 = added by hand, 0 = from
-- the source or a playlist made on the phone. Synced with the playlist's
-- tracks; a PUT track without the key is 0.
ALTER TABLE playlist_tracks ADD COLUMN added INTEGER NOT NULL DEFAULT 0;
