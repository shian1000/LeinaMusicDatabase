# Handoff for the MusicDatabaseApp agent: YouTube links set by hand, and the music video column

Written by the MusicDatabase agent (2026-10-15) in answer to the app's
`docs/handoffs/musicdatabase-youtube-links-by-hand.md`. Built as asked. The column name is
unchanged; the details you might notice are below.

## The column

Migration `0012_song_music_video.sql` adds `songs.music_video_youtube_id VARCHAR`, as asked. It
reaches the shared `music.db` with the next sharing copy after the owner starts MusicDatabase
(migrations run at startup).

- It holds a bare 11-character video id, or NULL. There is no "N/A".
- The owner sets it by hand: Song actions → "Set music video link". It shows in "Show links" and
  "Remove links" can clear it.
- MusicDatabase never searches for music videos. The column is never played, never put in a
  YouTube playlist, never used by the matcher and never added to `bad_youtube_videos`.

## Corrections of the two YouTube columns

Both are now known to the review and the automatic pass, labelled "YouTube video" and "music
video". The format is unchanged.

- **`youtube_video_id`:** a video id, `'N/A'` or NULL are all accepted values. NULL means "no link,
  search again".
- **`music_video_youtube_id`:** a video id or NULL. `'N/A'` can't be stored there and is offered
  only for editing.
- A link instead of a bare id is turned into the id. You send ids anyway.
- A correction made before the column existed (`old_value` NULL) is not flagged as "changed
  since", because the column starts empty.

**Accepting a `youtube_video_id`** is treated as the owner's own pick: the video comes off
`bad_youtube_videos` (for every song and for this song). The owner's answer: the review then asks,
default no, whether the replaced video should be remembered as `wrong_for_song`. When it is, your
existing `bad_youtube_videos` handling keeps that video away from the song.

**Automatic saving** (the owner's answer): yes, from users with `apply_corrections`, by the usual
rules (value storable, music.db still holds your `old_value`, no disagreeing reports, the song
still has that title). A video id is also checked: it must play here (Data API, then yt-dlp) and
must not be in `bad_youtube_videos` for the song. If YouTube blocks the check, the correction goes
to review. `'N/A'` and NULL need no check. Everything else is reviewed. The review tells the owner
that Leina Radio is already using the link and that rejecting it leaves your copy on it.

**Older link reports:** a newer `youtube_video_id` correction for a song settles older
`dead_youtube_links` and `found_youtube_links` reports for that song that are in the same batch,
without a search. Only the correction is asked about.

## Taking a correction back

Checked, and it works for every column. Your newer report with `new_value` = `old_value` is grouped
with the older report of that field, and the newest one wins. When music.db still has that value,
the case is settled as "database already has that value" and both files are done.

One gap is now fixed. The automatic pass reads only files nobody has looked at yet. If the older
report was already waiting for the owner's review when your takeback arrived, the takeback was
settled alone and the older report would later have been offered on its own. A settled "already
has that value" is now recorded as of the newest report, so any older report of the same field is
treated as answered.

If the owner had already *accepted* the older correction, your takeback is a real change back.
It always goes to review, flagged as "changed since your copy", because music.db no longer holds
the `old_value` you sent.
