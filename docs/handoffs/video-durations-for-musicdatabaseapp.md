# Handoff for the MusicDatabaseApp agent: YouTube videos' lengths in music.db

Written by the MusicDatabase agent (2026-10-27). This one isn't an answer: the
owner asked for it on the MusicDatabase side first. Please build the app side
described below, and write back in `docs/handoffs/musicdatabase-video-durations.md`
if something doesn't fit.

**Status (2026-10-27):** done on both sides. Leina Radio works out a playlist track's length
when reading (own length, then this table, then `files.db`) and shows "ponad …" while some are
unknown; song info / search lengths (the optional part) aren't done.

## Why

The owner wants Leina Radio to show how long a playlist is, including its
songs from music.db. MusicDatabase now asks the YouTube Data API how long its
songs' videos are and keeps the answers in `music.db`, so the phone gets them
with its usual download. There's no new endpoint or file.

## The table

Music.db migration 0017. The real database gets it the next time the owner
starts MusicDatabase. Before that, and in an older copy, the table is missing,
so check `sqlite_master` first, as for `bad_youtube_videos`.

```sql
CREATE TABLE youtube_video_durations (
  video_id   TEXT PRIMARY KEY NOT NULL, -- a YouTube video id
  duration_s INTEGER NOT NULL,          -- seconds, always > 0
  fetched_at TEXT NOT NULL              -- when YouTube was asked, 'YYYY-MM-DD HH:MM:SS' (UTC)
);
```

- **Keyed by video, not by song.** Join it on the song's current link:
  `LEFT JOIN youtube_video_durations d ON d.video_id = songs.youtube_video_id`.
  If a song's link changes, the join gives the new video's length or nothing,
  never the old one.
- **Not every video has a row.** These have none:
  - a live or upcoming stream (YouTube says 0);
  - a video YouTube doesn't list (gone, private);
  - a link MusicDatabase hasn't asked about yet.

  Treat a missing row as "length unknown", never as 0. `songs.youtube_video_id`
  = `"N/A"` (no video) never has a row.
- **What fills it.** For now only Manage database → "Find full albums". It asks
  about the videos of every song not marked as any kind (so not about
  `kind = 'full_album'` rows), and only about videos not stored yet. Expect most
  linked songs to have a length once the owner runs it; new links get one the
  next time it runs.
- **Rows may stay after use.** A row whose video no song uses any more is kept.
  Don't count rows; always go through the join.
- **Public copy.** The GitHub release has the table too (allow-listed). An
  install without the sharing server gets it the same way.

## What the app should do

1. **Playlist length.** `PlaylistLibrary.load()` sums `playlist_tracks.duration_s`,
   which tracks added from music.db often don't have. Work out a track's length
   in this order:
   1. its own `duration_s` (what the source said: the YouTube playlist, Spotify,
      the file);
   2. `youtube_video_durations` for the video it plays, either its own
      `youtube_video_id` or the music.db song's (`main_db_id` → `songs.youtube_video_id`);
   3. the library file's `files.db` `duration_s` for that song, when the track
      plays from the library.

   Do this when reading, from the downloaded music.db. Don't write the length
   back into `playlist_tracks`: those rows sync to the server, and a refreshed
   music.db would then disagree with them.
2. **Partly unknown lengths.** When some tracks still have no length, show the
   sum as at least that much ("ponad 1 godz. 12 min", or "1 godz. 12 min + 3
   utwory bez długości"), not as the whole length.
3. **Optional.** The same length can be shown in song info and in search
   results before the video loads, and used by the player as the expected
   length until the stream says otherwise.

## Not in this change

- MusicDatabase doesn't fill the table when it saves a new link (an import, a
  found link, a correction). That may come later. The table's shape won't
  change for it.
- The phone doesn't send lengths back. The phone already knows the length of a
  video it played; if the owner wants those sent, that needs a new request
  table, so ask here first.
