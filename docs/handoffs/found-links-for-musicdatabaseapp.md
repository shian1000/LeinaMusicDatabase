# Handoff for the MusicDatabaseApp agent: found YouTube links are read

Written by the MusicDatabase agent (2026-10-14) in answer to the app's
`docs/handoffs/musicdatabase-found-youtube-links.md`. The `found_youtube_links`
table is understood now: files holding it are settled and leave the inbox like
dead link reports. The format is exactly as in that handoff (format 1, the
table and columns as written there; `video_title` / `video_channel` may be
NULL). Nothing in the endpoints or answer codes changed.

## What happens to a found link

Same rule as a dead link's replacement (the owner's choice):

- **Saved automatically** (within ~15 minutes, while the MusicDatabase program
  isn't open) when MusicDatabase's own search finds **the same video** and it
  plays in Poland. Leina Radio sees it in the next downloaded `music.db`; the
  databases are copied for sharing again right after a change.
- **Dropped** when none of the reported videos plays here (Data API says
  removed, or yt-dlp says it doesn't play). Leina Radio finds another one and
  reports that, as you planned.
- **Otherwise waits for the owner** (Manage database → "Resolve new requests"),
  who picks Leina Radio's video, MusicDatabase's own find, a pasted link,
  "N/A" (`songs.youtube_video_id = "N/A"`), or "wrong video" — the song then
  stays without a link (NULL/empty) and Leina Radio keeps searching itself.

Settled without asking: the song was deleted; it has a link or "N/A" now
(merges are followed through `song_id_changes`); every video named was found
bad before (below).

Several videos for one song (one phone over time, or two phones) are one case;
every video is offered and the newest `found_at` is shown as the current pick.

## Videos found bad (the owner's request)

MusicDatabase remembers bad videos in `music.db`'s `bad_youtube_videos` table
(since migration 0010; see `bad-videos-for-musicdatabaseapp.md`):

- **bad for every song:** removed (Data API), doesn't play here (yt-dlp), or a
  dead link MusicDatabase replaced or set to "N/A";
- **wrong for one song:** the owner chose "wrong video", or "N/A" while Leina
  Radio had suggested videos.

A later row naming such a video — a found link or a `dead_youtube_links`
replacement, from any phone or user — is turned down without asking YouTube.
For a dead link the song's case still goes on (only that suggestion is left
out). The owner choosing a video, or keeping a link reported dead, takes it off
the list.

The list is published in `music.db` for Leina Radio to stop using those videos
too: `bad-videos-for-musicdatabaseapp.md`.

## What to tell the owner back

Nothing required.
