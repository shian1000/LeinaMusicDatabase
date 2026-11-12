# Handoff for the MusicDatabaseApp agent: other titles of a song

Written by the MusicDatabase agent (2026-11-12). Nothing in the app breaks
without it; it's for search.

## The column

Migration `0023_song_other_titles.sql` adds `songs.other_titles`, nullable
text. It reaches the shared `music.db` (and the public GitHub copy) with the
next sharing copy after the owner starts MusicDatabase.

- Several titles in one text, separated by `;` (not a comma: a title or a
  line of lyrics may hold one). Split on `;` and trim; empty pieces mean
  nothing. NULL = none.
- Never the song's own `title` or `title_latin` again.
- What it holds: the title in other languages ("Херувимская песнь" also
  "Hymn of the Cherubim; Cherubic Hymn"), a wrong title people know the song
  by better than the real one, a line of its lyrics ("Lubiła tańczyć" for
  "Na jednej z dzikich plaż"), and an older spelling of a title the owner
  corrected (a YouTube-translated "Stay" kept on "Залишайся").

## What the app could do with it

- Search: count each piece as a title, the way `title_latin` is counted.
- Matching a played video / a request to a song: the same, if the app has
  its own title matching. MusicDatabase's matchers all do now.
- Showing it is optional; the owner hasn't asked for that.

## Titles that changed on 2026-11-12

The owner had about 90 titles and 40 artist names put into their own
alphabet (Belarusian Lacinka -> Cyrillic, romaji -> Japanese, "Severniy
veter" -> "Северный ветер"), each with its old Latin spelling as
`title_latin` / `name_latin`. Song ids of the songs that were merged went
through `song_id_changes` as usual.
