# Handoff for the MusicDatabaseApp agent: lyrics from the server

Written by the MusicDatabase agent (2026-11-12). Nothing in the app breaks
without it: today's LRCLIB lookup (`lib/src/data/lyrics.dart`, `LyricsFinder`)
stays as the fallback (owner's decision).

## What changed on the server side

MusicDatabase now collects songs' lyrics itself (Fill missing data ->
"Lyrics": the lyrics in the library's mp3 tags, then LRCLIB, then Genius),
checked against the song: same title (or one of `other_titles`), resembling
artist. Synced lines are kept only when the found recording's length is
within 3 s of the song's own (its YouTube video / library file); otherwise
only the plain text is stored. They
live in `lyrics.db` next to `music.db`. The phone **doesn't download it**
(~25 MB); the sharing server answers one song at a time.

## The endpoint

`GET /lyrics/<songs.id>` (also `HEAD`), same server, port and TLS as the
databases, **no token**:

- 200, `application/json; charset=utf-8`:

  ```json
  {"song_id": 4, "plain": "Raz\nDwa", "synced": "[00:01.00] Raz\n[00:02.50] Dwa",
   "instrumental": false, "source": "lrclib_fetcher"}
  ```

  - `plain`: the text, lines by `\n`, or null.
  - `synced`: LRC, the format `parseLrc()` already reads, or null (plain only -
    often on purpose: the lines' times didn't fit the song's video). With
    null, show the plain text; don't go to LRCLIB for synced lines, they were
    turned down.
  - `instrumental`: true = the song has no words (`plain` and `synced` null);
    show that rather than "no lyrics".
  - `song_id`: the song the answer is for - not the one asked when that song
    was merged into another since the phone's `music.db`.
  - `source`: where the text came from (`lrclib_fetcher`,
    `library_tags_fetcher`, `genius_lyrics_fetcher`, `musixmatch_fetcher`,
    `lyricstranslate_fetcher`, `tekstowo_fetcher`, `fajnanuta_fetcher`, `cuspajz_fetcher`, `manual` = pasted by the owner, `original` = a
    live/remix version showing its original's words). Only informative;
    unknown values may come.
- 404: MusicDatabase has no lyrics for the song (or it was deleted, or the
  server has no `lyrics.db` yet) -> **fall back to `LyricsFinder`** as today.
- 503 / no answer / no server configured -> the same fallback.

## What the app could do

1. In the full player's lyrics card: ask the server first (when one is set),
   then LRCLIB on 404 or any failure. Keep it in memory for the run, as now.
2. "Pobieraj teksty piosenek" (Oszczędzanie danych) may keep covering both;
   PRIVACY.md's LRCLIB row then applies only to the fallback, and the server
   row (if it lists what the server is asked) gains the song id.
3. Songs whose `songs.language` is `Instrumental` have no words: the app
   may skip the lookup for them altogether (MusicDatabase never looks them up).
4. Nothing to store, no schema change, no new permission.
