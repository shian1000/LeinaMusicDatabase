-- Tracks the user removed from an imported playlist, so importing it again
-- (on any phone) leaves them out (playlist_sync.py; the app's
-- docs/handoffs/musicdatabase-excluded-tracks.md): a JSON list of the ids in
-- the playlist's source - YouTube video ids, Spotify track ids. NULL = none.
-- Synced with the playlist as a whole, like its tracks; a deleted playlist
-- (a marker row) has none.
ALTER TABLE playlists ADD COLUMN excluded_source_tracks TEXT;
