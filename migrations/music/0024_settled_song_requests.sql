-- Songs Leina Radio asked to have added (the `song_requests` table of a
-- request file) that MusicDatabase settled as one of its songs: found in the
-- database already, or added. The counterpart of rejected_song_requests
-- (migration 0015): the MusicDatabaseApp phone client (app handoff
-- docs/handoffs/settled-song-requests-for-musicdatabaseapp.md) recognises a
-- track it asked about as song_id even when nothing else connects them - a
-- file on the phone with no video, a name its matcher can't reconcile.
-- artist / title / youtube_video_id / spotify_track_id are as the request had
-- them (artist NULL = the phone knew none). Written by
-- utils/database/inbox_song_requests.py; a later request matching a row is
-- settled as that song without matching again.
CREATE TABLE IF NOT EXISTS settled_song_requests (
	id               INTEGER PRIMARY KEY,
	artist           TEXT,
	title            TEXT NOT NULL,
	youtube_video_id TEXT,
	spotify_track_id TEXT,
	song_id          INTEGER NOT NULL,
	settled_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_settled_song_requests_song_id ON settled_song_requests (song_id);

-- song_id stays a current songs.id, as in additional_youtube_videos: a merged
-- song's requests move to the kept song, a deleted song's go.
CREATE TRIGGER IF NOT EXISTS settled_song_requests_follow_songs
AFTER INSERT ON song_id_changes
BEGIN
	UPDATE settled_song_requests SET song_id = NEW.new_song_id
		WHERE song_id = NEW.old_song_id AND NEW.new_song_id IS NOT NULL;
	DELETE FROM settled_song_requests WHERE song_id = NEW.old_song_id;
END;
