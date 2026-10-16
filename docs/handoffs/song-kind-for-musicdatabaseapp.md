# Handoff for the MusicDatabaseApp agent: full albums in music.db (songs.kind)

Written by the MusicDatabase agent (2026-10-16). Unlike the other handoffs
this one isn't an answer: the owner asked for it on the MusicDatabase side
first. Please build the app side described below, and write back in
`docs/handoffs/musicdatabase-song-kind.md` if something doesn't fit.

## Why

Some rows of `songs` aren't songs. They are whole albums played through: the
YouTube link is a "full album" video, and the library mp3 is the whole album.
The owner wants them marked as albums and searchable as such, in MusicDatabase
and in Leina Radio.

## The column

Migration `0014_song_kind.sql` adds one nullable text column, `songs.kind`.
It reaches the shared `music.db` with the next sharing copy after the owner
starts MusicDatabase. Every row starts NULL.

- `NULL` is an ordinary song.
- `'full_album'` means the row is a whole album in one recording.
- More values may come later (a live set, a DJ mix). Treat any value you
  don't know like a song, and don't crash on it.

These rows are still song rows, on purpose. `files.db`, playlists, moods,
tags, listens, `bad_youtube_videos` and your link cache all keep working by
`songs.id`. A full album plays, sits in playlists and gets reported like any
song. Its `youtube_video_id` follows the same sentinels as before.

**Album link.** A full album is filed under the album it plays.
`songs.album` / `album_id` point at that album (cover, year and genre come
from there). When the owner marks a row whose album was empty or "Singles",
MusicDatabase sets its album to its own title. So a full album usually has
`songs.title == albums.title`.

Older app versions ignore the column, so nothing breaks before you ship.

## What the app should do

1. **Show it.** Show a label or icon ("Album" / "Cały album") wherever a row
   appears: search results, playlists, the player, song info.
2. **Search.** Full albums are found like songs, by title and artist. Add a
   way to list or filter only full albums (a filter chip, or a separate
   "Albumy" section in the results). Ordinary search should keep finding
   them too.
3. **Radio and shuffle.** Leave `kind = 'full_album'` rows out of the song
   radio and other automatic picks (genre radio, "similar songs", random
   play). A 45-minute album shouldn't land in the middle of a mix. Playing it
   on purpose (search, a playlist, an album view) is fine. Within a
   user-built playlist, keep it where the user put it.
4. **Your own YouTube search.** For rows with no link, either skip full
   albums, or search for `"<artist> <title> full album"` and accept only long
   videos (around 20 minutes or more). Never pick a single track for them.
   Found links go through the usual found-links table, as now.
5. **Album view (optional).** If the app shows an album, a full-album row
   with that `album_id` can be offered as "Posłuchaj całego albumu".

## Marking from the app (the owner asked for this)

The user must be able to say, from Leina Radio, that a song is really an
album (or that a marked one is really a song). Send it as an ordinary field
correction in the request file's `field_corrections` table (format 1,
unchanged):

| table_name | column_name | new_value                      | old_value               |
|------------|-------------|--------------------------------|-------------------------|
| `songs`    | `kind`      | `full_album`, or empty/NULL = a song | the value in your music.db |

- `("songs", "kind")` is in MusicDatabase's accepted fields now (labelled
  "song or full album" in the review). Anything other than `full_album` or
  empty is refused, so send exactly those.
- Same review rules as other corrections. A user with `apply_corrections` gets
  it applied by the automatic pass when nothing conflicts; everyone else's
  waits for the owner. Until music.db's value changes, show the user's choice
  locally, the same way as other pending corrections ("Wysłano poprawkę").
- Taking it back works as before: send new_value == old_value.
- Applying `full_album` to a row with no album (or "Singles") also sets its
  album to its own title. That change comes from MusicDatabase, so don't send
  an album correction for it.

A suggested place in the UI is song info → a toggle "To jest cały album".

## Since the first version (same day)

- **Finding them.** Manage database → "Find full albums" lists songs whose
  title says "(Full Album)" / "cały album" / "весь альбом" and songs with a
  library file or YouTube video of 20 minutes or more. The owner ticks the
  real albums. Their titles may lose the "(Full Album)" words then, so
  `songs.title` of such a row changes once. Expect the first batch of
  `full_album` rows (about a dozen) after the owner runs it.
- **Imports.** A song imported with "(Full Album)" (or similar) in its title
  is marked `full_album` at once.
- **Our YouTube search** looks for `"<artist> - <title> full album"` and
  takes only videos of 20 minutes or more. If your own search does the same,
  your and our picks will agree more often for found-link reports.
