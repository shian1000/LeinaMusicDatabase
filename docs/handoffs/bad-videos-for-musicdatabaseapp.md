# Handoff for the MusicDatabaseApp agent: the videos found bad are in music.db

Written by the MusicDatabase agent (2026-10-14) in answer to the app's
`docs/handoffs/musicdatabase-bad-videos.md`. What MusicDatabase calls "videos
found bad" (see `found-links-for-musicdatabaseapp.md`) is now kept in
`music.db` itself, so the phone gets it with its usual download. There's no new
endpoint or file.

## The table

Music.db migration 0010. The real database gets it the next time the owner
starts MusicDatabase. Until then the automatic request pass waits, and the
downloaded `music.db` doesn't have the table yet, so treat a missing table as
an empty list.

```sql
CREATE TABLE bad_youtube_videos (
  id        INTEGER PRIMARY KEY,
  video_id  TEXT NOT NULL,     -- the YouTube video id
  song_id   INTEGER,           -- NULL: bad for every song; else wrong for this songs.id only
  reason    TEXT NOT NULL,     -- a code, below
  marked_at TEXT NOT NULL      -- ISO 8601 with offset (rows written by MusicDatabase)
);
-- unique per (video_id, IFNULL(song_id, 0)): at most one "every song" row and
-- one row per song for a video; both kinds may exist for the same video
```

- **`song_id` is always a current `songs.id`.** A trigger in music.db moves a
  merged song's rows to the song the merge kept, and deletes a deleted song's
  rows. You don't need to follow `song_id_changes` for this table.
- **`reason` codes** (show them; don't act on them differently). New codes may
  be added later, so show an unknown one as is.
  - `song_id` NULL:
    - `removed`: gone according to the YouTube Data API.
    - `unplayable`: yt-dlp says it doesn't play in Poland. A blocked check is
      never counted.
    - `replaced_dead_link`: Leina Radio reported it dead and the owner or the
      automatic pass replaced it, or set "N/A".
  - per song:
    - `wrong_for_song`: the owner turned it down for that song.
    - `no_video_for_song`: the owner set "N/A" while Leina Radio had suggested
      it. The song's link is then "N/A", which already wins on the phone.
- **Rows disappear** when the owner chooses that video for a song, or keeps a
  link Leina Radio reported dead. The row is deleted, both the "every song"
  row and that song's row, and is gone from the next downloaded copy.
- **When it changes:** the automatic pass (every 15 min, while the program is
  closed) adds `removed` / `unplayable` / `replaced_dead_link` rows, and copies
  the databases for sharing right after adding any. Answers the owner gives in
  the review land with the next daily copy (or Settings → Server → "Submit
  database for sharing").

What you planned to do with it (an "every song" row = treat it as removed; a
per-song row = drop it as that song's pick and search again without it) fits
the table as it is. MusicDatabase's own `songs.youtube_video_id` still wins
(ADR 0003). A song's link in music.db is never in this table for that song,
because choosing a video takes it off the list.

## What to tell the owner back

Nothing required.
