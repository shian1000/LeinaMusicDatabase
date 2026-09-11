-- Manual override for songs whose correct YouTube video can never be found
-- by automated search — e.g. a video YouTube itself excludes from search
-- results entirely for being age-restricted (verified: happens even for an
-- authenticated Data API request, not just anonymous yt-dlp scraping — see
-- docs/agent-notes/youtube-search-matching.md, Taco Hemingway - "Fuck Your
-- List"). When set, search_video()/search_video_ytdlp() skip searching
-- entirely and use this value, so a once-confirmed manual find survives a
-- cache clear/re-sync instead of needing to be re-supplied every time.
ALTER TABLE songs ADD COLUMN youtube_video_id VARCHAR;
