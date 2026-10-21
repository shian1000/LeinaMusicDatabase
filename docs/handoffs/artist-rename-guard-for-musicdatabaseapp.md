# Handoff for the MusicDatabaseApp agent: a rename sent where another artist was meant

Written by the MusicDatabase agent (2026-10-18). Follow-up to the app's
`docs/handoffs/musicdatabase-song-artist.md` and our
`song-artist-for-musicdatabaseapp.md`.

## What happened

Song #4771 "Hard Times (Paramore)" (album "David Byrne Does Hard Times"), artist
Paramore (#1559). The owner changed the artist to "David Byrne" in Leina Radio,
meaning *this song is David Byrne's*. The request file
`<owner>_2026-10-17_11-54-27_019.db` (made 2026-10-17 11:54, after commit
`975944c` of 00:10 the same day) carried:

| table_name | row_id | column_name | old_value | new_value | song_id |
|---|---|---|---|---|---|
| `artists` | 1559 | `name` | Paramore | David Byrne | 4771 |

That is the spelling correction ("Popraw pisownię nazwy"), not the
`songs` / `artist_id` one ("Ten utwór ma innego wykonawcę"). MusicDatabase did what
the row says: it offered renaming the artist, and all 5 Paramore songs became
"David Byrne" once it was accepted. The owner had to repair that by hand.

## What to change in Leina Radio

1. **Find out which path sent it.** In `lib/src/widgets/song_info.dart`,
   `_mainArtistNameField()` falls straight back to
   `_editableField('artists', artist, 'name', …)` (a plain rename, no question)
   when `outbox == null`, `widget.hit.mainSong == null`, `song['id']` /
   `song['artist_id']` isn't an int, or the name is null. Check whether one of
   those holds for a song like #4771 (e.g. opened from a list where `hit.mainSong`
   is missing), whether the installed build was older than `975944c`, or whether
   the spelling option was simply picked. Fix whichever it was.

2. **No silent rename fallback.** The song's own artist must always get the
   two-way question. If the "another artist" correction really can't be built
   (no song id), don't offer editing the name there at all rather than offering
   only a rename. A rename stays possible from the artist's own place, never as
   the only thing the song's pencil does.

3. **Guard the spelling path.** When the name typed under "Popraw pisownię
   nazwy" doesn't resemble the current one (after `normalize()`, a simple
   similarity check — "Paramore" → "David Byrne" clearly isn't a spelling fix),
   ask before queueing it, e.g. "To nie wygląda na poprawkę pisowni. Czy ten
   utwór ma innego wykonawcę?" with "Tak, tylko ten utwór" (send the
   `songs` / `artist_id` correction with the typed name instead) and "Nie, zmień
   nazwę we wszystkich utworach". The first option comes first.

4. **Outbox.** A waiting (not yet sent) `artists.name` correction whose new name
   doesn't resemble the old one: worth showing the same hint in "Zgłoszenia dla
   MusicDatabase" so the user can switch it before it goes out.

5. **Co-artists / guests** ("Współwykonawca", "Gościnnie") keep only the spelling
   correction, as now — the guard from point 3 applies to them too (a warning
   only; there's no "another artist" correction for them yet).

No format change: both correction kinds stay exactly as described in
`musicdatabase-field-corrections.md` and `musicdatabase-song-artist.md`.

## What MusicDatabase now does on its side

A safety net, not a replacement for the above:

- An `artists.name` correction whose new name doesn't resemble the old one
  (`text_utils.title_resembles()`) is flagged in review ("doesn't look like a
  spelling fix"), and the first choices are "Only this song: credit it to …" —
  the correction's `song_id` moves to that artist (existing or new) as if a
  `songs` / `artist_id` correction had come; renaming the artist is still
  offered below. Only when that artist is the song's primary one.
- The automatic pass never saves such a rename, even from a user with
  `apply_corrections`; it goes to review with that note.

So `song_id` on an `artists.name` row matters: keep it the song the user was
looking at.
