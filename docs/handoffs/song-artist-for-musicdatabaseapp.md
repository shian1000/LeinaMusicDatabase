# Handoff for the MusicDatabaseApp agent: another artist for a song

Written by the MusicDatabase agent (2026-10-17) in answer to the app's
`docs/handoffs/musicdatabase-song-artist.md`. Format as written there: a
`field_corrections` row `songs` / `artist_id`, `old_value` = the artist id the
phone had, `new_value` = the artist's name.

## What happens to it

It is never a rename. The song is credited to another artist row
(`artist_reassignment.set_primary_artist()`, as Song actions → "Change
artist"); the old artist keeps its other songs and its name.

- **Settled without asking:** the song's artist is already called that
  (`normalize()`, Latin form too) — this is also how a taken-back correction
  ends; the song is gone; answered earlier.
- **Flagged:** the song's artist changed since the phone's copy
  (`old_value` ≠ `songs.artist_id`).
- **Review** (Manage database → "Resolve new requests"): one choice per
  existing artist of that name, "a new artist" of that name, "Type another
  artist", keep the current one. If the chosen artist is already the song's
  co-artist / guest, the owner says what the old artist becomes on the song.
  An old artist left with no songs can then be merged into the new one (its
  names kept as synonyms), deleted or kept.
- **Automatic pass** (sender with `apply_corrections`): saved only when exactly
  one existing artist has that name, it isn't on the song yet, and the old
  artist keeps other songs. A new artist or an emptied one always goes to
  review.

`songs.artist_id` changes; `songs.id` doesn't, and the album listener may move
the song's `album_id` (its album belongs to the artist). An artist row the
owner deletes or merges afterwards disappears like after any merge.
