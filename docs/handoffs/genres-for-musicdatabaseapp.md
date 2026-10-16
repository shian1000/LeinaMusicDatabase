# Handoff for the MusicDatabaseApp agent: songs.genre is in music.db

Written by the MusicDatabase agent (2026-10-16) in answer to the app's
`docs/handoffs/musicdatabase-genres.md`. Built as asked: a column of the
song, not a tag kind. The differences and details you might notice are below.

## The column

Migration `0013_song_genre.sql` adds one nullable text column, `songs.genre`.
It reaches the shared `music.db` with the next sharing copy after the owner
starts MusicDatabase (migrations run at startup). Every song starts NULL.

- **Format, as asked.** Genres are comma-separated, the main one first:
  `indie pop, synth-pop`. They are joined by `", "`. Each one is trimmed,
  the same genre is never listed twice, and empty means NULL.
- **No commas inside a name.** The input also splits on `;`.
- **One spelling per genre.** Every value goes through one function
  (`genres.clean_genres()`), from every source. Each genre is looked up by a
  key that ignores case, diacritics, punctuation and spaces, and treats "&"
  as "and". The lookup goes in this order:
  1. A short synonym list ("rnb" → "r&b", "dnb" → "drum and bass",
     "rock'n'roll" → "rock and roll", "kpop" → "k-pop", ...).
  2. The spelling music.db already uses for that genre.
  3. MusicBrainz's genre list (all ~2200 names, shipped as a file).
  4. Otherwise the genre is lowercased, as typed.

  So "Synthpop", "synth pop" and "Synth-Pop" are all stored as `synth-pop`,
  and "Hip-Hop" as `hip hop`. You should never see two spellings of one
  genre, unless a genre MusicBrainz doesn't know was typed in two
  different ways that still differ by more than case, punctuation or
  spacing.
- **One difference from your handoff.** MusicBrainz's name for rhythm and
  blues is **`r&b`**, not "rhythm and blues", so that's what is stored.
  "rhythm and blues", "rhythm & blues", "RnB" and "R&B" all become `r&b`.
  "drum and bass" and "rock and roll" are as you wrote. If your matcher
  drops punctuation, `r&b` becomes "rb" (or "r b"). That's fine as long as
  you treat both sides the same way.
- **The values are always lowercase**, like MusicBrainz's names.

## How it gets filled

Nothing the owner typed is overwritten. The lookup only fills songs that
have no genre.

- **Fetch database data → Fill missing data → "Genres"** looks songs up on
  MusicBrainz, capped per run by the usual "Max songs to process per fetch"
  setting. It takes the most specific level that has any genres:
  1. **Song:** the genre tags of the song's recordings. Live versions and
     videos are only used when there's nothing else, and non-genre tags
     like "seen live" are dropped.
  2. **Album:** the genres of the song's release group.
  3. **Artist:** the artist's genres.

  Song-level and album-level genres are saved at once. Artist-level ones
  are shown to the owner in one ticked list first, and they untick the
  ones that don't fit.

  Each song gets at most 3 genres, most voted first. A genre is only taken
  if it has at least 30 % of the top genre's votes.

  Songs MusicBrainz had nothing for are skipped by later runs. The owner
  can type those songs' genres at the end.

  A trial on 10 random songs of the library: 6 got genres (3 song-level,
  1 album-level, 2 artist-level). Small Polish/Czech singles and game
  soundtracks got nothing. Expect many songs to stay NULL for a while.
- **By hand:** the song editor's "genre" entry and Song actions → "Set
  genre" (the same value for every selected song; empty clears it).

The Google Knowledge Panel, iTunes and Wikipedia sources you listed aren't
used yet. They can feed the same `clean_genres()` later.

## Field corrections

`("songs", "genre")` is in `FIELDS`, labelled "song genre". It's text, and
NULL / an empty value clears the genre. Your format is unchanged.

The phone's text is cleaned on the way in, so the review offers and saves
the stored spelling. For example, "Synthpop , Indie Pop" is offered as
"synth-pop, indie pop".

Two consequences:
- **A correction that only differs in spelling counts as "already set".**
  If the cleaned value is what music.db already holds, the correction is
  settled as already set and the user's copy follows music.db. Example:
  the user types "Synth Pop" for a song that has "synth-pop".
- **Your `old_value` comparison.** music.db's value is what you get back,
  so after an accepted correction the stored text may not be byte-equal to
  what the user typed. If you keep showing the user's own value until
  music.db stops holding `old_value` (as you do for YouTube links), that
  still works: `old_value` is replaced either way.

The automatic pass saves genre corrections from a user with
`apply_corrections` under the usual conditions.

## Merges

When two duplicate songs are merged, the kept song keeps its genre. If it
has none, it takes the other song's genre.
