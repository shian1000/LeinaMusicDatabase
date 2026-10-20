# Handoff for the MusicDatabaseApp agent: songs to add are read

Written by the MusicDatabase agent (2026-10-18) in answer to the app's
`docs/handoffs/musicdatabase-song-requests.md` (both sides were built in the same change). The
`song_requests` table is understood exactly as described there; nothing in the endpoints or
answer codes changed. `title`, `source` and `requested_at` are required (a file without them is
rejected as unreadable); the other columns may be missing or NULL.

## What happens to a request

- **Never added automatically** (the owner's decision). The automatic pass only settles a song
  that is in the database already (by artist + title, or its YouTube video is a song's link) or was
  rejected before; everything else waits for the owner in "Resolve new requests".
- The owner **adds** it (as asked, or with the artist / title corrected): it goes through the
  normal import (MusicBrainz spelling, similar artist / song questions), with the request's album,
  year and YouTube video. Leina Radio sees it in the next downloaded `music.db`.
- **Skips** it: the file stays in the inbox for next time.
- **Rejects** it: one row per distinct way the reports named it goes to music.db's
  `rejected_song_requests` (migration 0015, columns as in your handoff). A later request matching
  a row by normalized artist + title, YouTube video or Spotify track is settled without asking.
  The table reaches the phone with the next copy for sharing (daily, or Settings → Server →
  "Submit database for sharing").

Several requests for one song (normalized artist + title) are one case; the newest spelling is
offered.

## What to tell the owner back

The real music.db gets migration 0015 the next time MusicDatabase starts (after its automatic
backup); until then the downloaded `music.db` has no `rejected_song_requests` - treat it as empty.
