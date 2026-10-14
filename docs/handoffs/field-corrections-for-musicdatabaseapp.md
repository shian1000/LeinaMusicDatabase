# Handoff for the MusicDatabaseApp agent: field corrections are read

Written by the MusicDatabase agent (2026-10-14) in answer to the app's
`docs/handoffs/musicdatabase-field-corrections.md`. The `field_corrections`
table is understood now: files holding it are reviewed and leave the inbox
like dead link reports. The request format is unchanged from that handoff
(format 1, the table and columns as written there).

## What happens to a correction

Nothing is applied automatically. The owner reviews them in Manage database →
"Resolve new requests", after the dead links. Per field they see the song, the
phone's old value, the database's current value and the correction, and choose:
save it / edit it first / keep the current value / skip for later.

Settled without asking (the file can still leave the inbox):

- the row is gone — a song deleted since (merges are followed through
  `song_id_changes`, so a merged song's correction lands on the kept song);
  an artist or album that was merged or deleted (there's no log for those, so
  such a correction is dropped, not moved);
- music.db already holds the new value;
- the owner already answered that field after the newest correction was made.

Asked, but flagged for the owner: the value changed since the phone's copy
(`old_value` ≠ current), the song now shows another title, or the artist /
album is no longer that song's.

## Choices you might see the effect of

- **Repeats:** grouped by (table, row, column); the newest `corrected_at` is
  offered, older values are only shown. One answer settles them all.
- **`songs.album`:** saving moves just that song to the album of the new name
  (MusicDatabase's album rows follow `songs.album` — the song may get another
  `album_id`). When the song's album has other songs, the owner can instead
  rename that whole album: same `album_id`, every song's `album` text changes.
- **`albums.title`:** the album keeps its id and all its songs' `songs.album`
  text changes with it. If the artist already has an album with that title the
  two are merged: the one with a cover (else the older id) is kept, so an
  `album_id` can disappear — the same as any album merge.
- **`artists.name`** that is already another artist's name: the owner can merge
  the artist into that one, so the corrected `artists.id` disappears and its
  songs move to the other id (an ordinary artist merge).
- **Values that can't be stored** — not a whole number for an integer column,
  an `albums.type` outside the list, an empty title or name — are offered for
  editing only. Integers sent as digit strings (`"1999"`) are accepted.
- A `(table, column)` outside the list is never applied; its file stays in the
  inbox for a later version.

## Rejected corrections

As expected on your side: a rejected correction leaves music.db at `old_value`,
so the phone keeps showing "Wysłano poprawkę". MusicDatabase records the answer
(not visible to the phone), and a newer correction of the same field — a later
`corrected_at` — is asked about again. Forgetting rejected ones on the phone is
still open; if you want to know, an answer channel would need designing.
