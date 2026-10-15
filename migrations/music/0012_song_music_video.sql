-- The song's music video on YouTube (official clip), apart from
-- youtube_video_id (the audio the MusicDatabaseApp phone client plays, often
-- a "Topic" upload). A bare 11-character video id, set by hand in the song
-- actions or from a reviewed phone correction (app handoff
-- docs/handoffs/musicdatabase-youtube-links-by-hand.md); NULL = none known.
-- No "N/A" sentinel: nothing searches for music videos. Only a link to open -
-- never used for playback, YouTube playlists, bad_youtube_videos or the matcher.
ALTER TABLE songs ADD COLUMN music_video_youtube_id VARCHAR;
