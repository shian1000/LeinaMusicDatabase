# Handoff for the MusicDatabaseApp agent: a song's additional YouTube videos in music.db

Written by the MusicDatabase agent (2026-10-31). This one isn't an answer: the
owner asked for it on the MusicDatabase side first. Please build the app side
described below, and write back in `docs/handoffs/musicdatabase-additional-videos.md`
if something doesn't fit.

**Status (2026-10-31):** done on the MusicDatabase side; the app side is not started.

## Why

A song has one link, `songs.youtube_video_id`. The same recording is often on
YouTube more than once (a Topic upload, the official video, a reupload). When
Leina Radio plays one of the others, from a YouTube playlist for example,
`matchSong()` doesn't find it by the video and falls back to artist + title. A
video titled "Солдат (The Soldier) Live at Orange Room, Kyiv 2022" or with a
misspelt artist then isn't recognised. MusicDatabase now keeps the other
videos it knows are the song, so the phone can recognise them by the video id.

## The table

Music.db migration 0018. The real database gets it the next time the owner
starts MusicDatabase. Before that, and in an older copy, the table is missing,
so check `sqlite_master` first, as for `bad_youtube_videos`.

```sql
CREATE TABLE additional_youtube_videos (
  video_id TEXT PRIMARY KEY NOT NULL, -- a YouTube video id; one song's only
  song_id  INTEGER NOT NULL,          -- a current songs.id (follows merges, gone with a deleted song)
  source   TEXT NOT NULL,             -- what added it: 'folder_playlist' | 'song_merge'; show-only, may grow
  added_at TEXT NOT NULL              -- 'YYYY-MM-DD HH:MM:SS' (UTC)
);
```

- **Only for recognising a video.** Never play these instead of the song's own
  link, never put them in playlists made from music.db, never count them as
  "the song has a link". The song's own link stays `songs.youtube_video_id`
  with its usual values (video id / `"N/A"` / NULL).
- **`songs.youtube_video_id` wins.** Look a video up there first, then here.
  MusicDatabase never puts a song's own link here, but a video may become some
  song's own link after it was added here; then the own link is the answer.
- **A song marked `"N/A"` can have rows.** "No video to play" stays true; the
  rows only say which uploads are that song (e.g. a live recording the owner
  doesn't want played as the song).
- **`song_id` is always current.** A trigger moves rows to the kept song on a
  merge and deletes them with a deleted song, as for `bad_youtube_videos`, so
  no `song_id_changes` walk is needed.
- **A video found wrong for a song** (`bad_youtube_videos` with that song) is
  removed from here by MusicDatabase. If both ever disagree, the bad mark wins.
- **Public copy.** The GitHub release has the table too (allow-listed).

## What the app should do

1. **`SongCatalog.findByYouTubeId(videoId)`** (used by `matchSong()` and the
   other video lookups): after no song has `youtube_video_id = videoId`, look
   `videoId` up in `additional_youtube_videos` and return that song. Tolerate
   the table being missing.
2. **Song requests.** A played video found this way is a known song: don't
   send a `song_requests` row for it. MusicDatabase already treats a requested
   video listed here as the song in the database.
3. **Optional:** song info may list the additional videos ("Also on YouTube as …").
