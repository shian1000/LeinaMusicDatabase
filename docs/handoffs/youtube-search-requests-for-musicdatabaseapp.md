# Handoff for the MusicDatabaseApp agent: YouTube search results asked for unparsed

Written by the MusicDatabase agent (2026-10-21) after a song request came in
as `artist = "familyboombox"`, `title = "Бумбокс - Твiй на 100%"`
(`youtube_video_id = Ws-hd5C5STg`, source `youtube`). That is the video's
channel and its whole title, exactly as YouTube has them. The song is
Бумбокс's "Твiй на 100%", and `familyboombox` is already a synonym of
Бумбокс in music.db.

## Where it comes from

`_YouTubeTile` in `lib/src/screens/song_search_screen.dart` (the YouTube
results of Wyszukaj) builds its hit from the raw video:

```dart
SearchHit.youTubeOnly(title: video.title, artistLabel: video.channel, videoId: video.id)
```

Playing it makes `SongRequest.of(hit)` ask for that raw artist and title.
Every other place that turns a video into a hit (`video_link.dart`,
`playlist_library.dart`) first runs `parseYouTubeTrack(video.title,
video.channel)`, which here gives `Бумбокс` / `Твiй на 100%`.

## What to change

- Run `parseYouTubeTrack()` in `_YouTubeTile` (or wherever the search
  results' hits are built) and use its artist and title for the hit, so both
  the request and what the row shows are parsed. Keep the video's raw title
  and channel wherever only the video itself is shown, if you think it reads
  better there, but the request must carry the parsed values.
- Probably worth it too: before treating the result as "youTubeOnly", look it
  up in the catalog by the parsed artist and title (`catalog.findExact()`, as
  `video_link.dart` does), so a song music.db already has isn't asked for.
- A test for the search result → request path with this case (channel
  `familyboombox`, title `Бумбокс - Твiй на 100%`).

No format change: `song_requests` stays as in
`docs/handoffs/musicdatabase-song-requests.md`.

## What MusicDatabase does meanwhile

For requests already sent like this, the review now suggests the split itself
(`inbox_song_requests.suggested_artist_title()`): when the part of the title
before " - " is an artist in music.db and the request's artist is that same
artist (by name, synonym or Latin form) or one music.db doesn't know, the
review offers "Add it as Бумбокс - Твiй на 100%" first, and the "already in
the database" check uses the split too. A known *other* artist's channel is
left as asked. This is only a fallback, and MusicDatabase still never adds a
song without the owner, so the fix belongs in Leina Radio.
