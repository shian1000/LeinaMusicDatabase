-- What kind of recording the song row is: NULL = an ordinary song,
-- 'full_album' = a whole album played through (one YouTube video / one mp3 of
-- the full album). Allowed values: constants.SONG_KINDS, set only through
-- utils/database/song_kinds.py. Read by the MusicDatabaseApp phone client
-- (docs/handoffs/song-kind-for-musicdatabaseapp.md) to label such rows and
-- keep them out of its song radio. Kept as a song row on purpose: files.db,
-- playlists, tags and bad_youtube_videos all point at songs.id.
ALTER TABLE songs ADD COLUMN kind VARCHAR;
