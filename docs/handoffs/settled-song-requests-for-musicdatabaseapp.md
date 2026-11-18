# Handoff for the MusicDatabaseApp agent: which song a song request became

Written by the MusicDatabase agent (2026-11-18). Not an answer: the owner asked for it
after a Leina Radio playlist track ("Paolo Nutini - Iron Sky [Short Film]") stayed "not in the
database" although MusicDatabase had settled its request as a song it already had. Write back
in `docs/handoffs/musicdatabase-settled-song-requests.md` if something doesn't fit.

**Status (2026-11-18):** built on both sides in the same change (app: `SettledSongRequests` in
`lib/src/data/song_requests.dart`, `SongCatalog.findSettledRequest()`).

## Why

Leina Radio recognises a track as a music.db song by `songs.youtube_video_id`,
`additional_youtube_videos`, `songs.spotify_url` or its own name matching. A request MusicDatabase
settled could match none of them: a file on the phone has no video, a Spotify track's song has no
`spotify_url`, and some names the phone's matcher can't reconcile ("[1-01] Cave Story (Main
Theme)" / "Cave Story Remastered Soundtrack", the artist and title swapped in the file's tags).
Until now only rejections came back (`rejected_song_requests`); this is the other half.

## The table

Music.db migration 0024. The real database gets it the next time the owner starts MusicDatabase;
before that, and in an older copy, the table is missing - check `sqlite_master` first.

```sql
CREATE TABLE settled_song_requests (
  id               INTEGER PRIMARY KEY,
  artist           TEXT,          -- as the request sent it; NULL = it had none
  title            TEXT NOT NULL, -- as sent
  youtube_video_id TEXT,          -- as sent
  spotify_track_id TEXT,          -- as sent
  song_id          INTEGER NOT NULL, -- the songs.id it is; always current
  settled_at       TEXT NOT NULL  -- when it was recorded
);
```

- **One row per distinct way a request named the song** (artist, title, video, Spotify track as
  sent), written when MusicDatabase found the requested song already in the database or the owner
  added it (`song_id` = the song added, or the one the import found it to be). Rejected songs stay
  in `rejected_song_requests` only.
- **Match a track the way `rejected_song_requests` is matched:** `songRequestKey(artist, title)`
  of the row, or its YouTube video, or its Spotify track. The names are stored exactly as sent, so
  the phone's own key reproduces them.
- **MusicDatabase's answer wins** over the phone's name matching; `songs.youtube_video_id` and
  `additional_youtube_videos` still come first for a video. A video MusicDatabase marked wrong for
  that song (`bad_youtube_videos`) doesn't count.
- **`song_id` is always current**: a trigger moves rows to the kept song on a merge and deletes
  them with a deleted song (as for `additional_youtube_videos`). The phone still goes through
  `current()`, which costs nothing.
- **Backfill:** requests settled before the table are written once, from MusicDatabase's
  `Inbox/processed/` and `decisions.json`, at the start that applies the migration (~206 of
  ~210 old requests on 2026-11-18).
- **Public copy:** the GitHub release has the table too (allow-listed, owner's choice 2026-11-18).

## What the app does

1. `SettledSongRequests.read(db)` with the catalog, next to `RejectedSongRequests`.
2. `SongCatalog.findSettledRequest(title:, artist:, videoId:, spotifyTrackId:)` - the request's
   names first (`SongRequest.unknownArtist` = no artist), then the video, then the Spotify track.
3. `playlistSongByName()` (playlists, moods, `playlistHit()`, writing `main_db_id` back into
   playlist tracks) tries it before `findByName()`, also for a track without an artist; the
   local library's matcher (phone files) does the same.
4. A hit settled so is never asked for again (`TrackPlayer._requestIfMissing()`).
