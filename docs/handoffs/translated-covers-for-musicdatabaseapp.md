# Handoff for the MusicDatabaseApp agent: translated covers (songs.cover_translated)

Written by the MusicDatabase agent (2026-11-20). Nothing in the app needs to change yet;
this says what the new column means, so the app can use it when the owner asks. Write back in
`docs/handoffs/musicdatabase-translated-covers.md` if something doesn't fit.

## The schema

Migration `0026_song_cover_translated.sql`: `songs.cover_translated INTEGER`.

- `1`: a translated cover - a cover (`covered_artist_id` or `covered_title` set, see
  [cover-songs-for-musicdatabaseapp.md](cover-songs-for-musicdatabaseapp.md)) with words of its
  own in another language than the original's: Mistmorn's Ukrainian "Дарма" of American
  Football's English "Never Meant", Lucie Vondráčková's Czech "Vítr" of "Holding Out for a Hero".
- `NULL`: the cover sings the original's words, or isn't marked yet; always NULL for a song that
  isn't a cover.

It reaches the shared `music.db` with the next copy for sharing. It is **not** in the public copy
on GitHub yet (left out of `public_export.MUSIC_SPEC`, so the column is there but NULL) until the
owner decides.

## What it changes on this side

- Lyrics (`GET /lyrics/<song id>`): a translated cover's lyrics are no longer looked up under the
  covered artist and the original's title, so the server's answer is the cover's own words (or
  none yet). If the app falls back to its own LRCLIB lookup, it should do the same: for a cover
  with `cover_translated = 1`, don't search with the original's artist or `covered_title`.
- `songs.language` of a cover that isn't translated is now taken from the original song when
  music.db has it; a translated cover's is its own.

## What the app may do (owner's call)

- Show the credit as "(Original cover, translated)" or similar - nothing is decided; until then
  show it like any cover.
- Don't treat a translated cover's text or language as the original's in song radio or stats.
