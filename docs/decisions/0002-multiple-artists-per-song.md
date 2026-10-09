# ADR-0002: Multiple artists per song via an `additional_song_artists` link table, with `songs.artist_id` kept as the primary artist

- Status: Accepted
- Date: 2026-10-09

## Context

Until now each song had exactly one artist (`songs.artist_id`), so a collaboration was stored as
one artist row with a joined name: "Sw@da x Maxim x Niczos", "Futro Ft. Fisz & Novika". About 10%
of `music.db`'s artists looked like this (~350 rows at the time), and the joined names kept costing
us in several places:

- "Songs by Maxim" didn't find his collabs, and origin and statistics were attached to joined names
  rather than to real people.
- The album fetchers and YouTube matching needed heuristics to cope with them: `truncate_at_word()`
  cutting off "& X" and "feat. X", `youtube_fetcher.py` re-joining artist links (commit
  `c2b7286`), and `_multi_artist_names_all_present()` guessing names with a regex split. That split
  guesses wrong for single names that contain a separator word ("Final Fantasy X OST").
- Collab sizes vary widely: 2 names ~300 times, 3 names ~50 times, up to 9 for one real song, and
  18 for one soundtrack credit.

## Decision

1. **A link table, not columns.** `additional_song_artists(song_id, artist_id, role)`, with
   primary key `(song_id, artist_id)` and one row per additional artist (migration
   `0003_additional_song_artists.sql`). It follows the same pattern as `song_tags`.
2. **`songs.artist_id` stays the primary artist.** Only multi-artist songs get rows in the new
   table, which holds the *additional* artists only. Single-artist songs, the large majority, are
   untouched, and the ~140 existing `song.artist` / `artist_id` call sites keep working. Code that
   needs *all* artists goes through `src/utils/database/song_artists.py`.
3. **`role` is `main` or `feat`.** `main` is a co-headliner ("A x B") and `feat` is a guest
   ("A feat. B"). The primary artist is always main.
4. **No ordering column.** Matching is order-independent. Labels put the primary artist first and
   sort the rest by name, which is enough for display. We can add an ordering column later if it
   turns out to be needed.
5. **Matching rules that follow from the roles.** When explicit credits are present, YouTube
   matching requires every `main` artist (in any order, synonyms count) for full artist relevance.
   A `feat` artist only earns a quality bonus, because uploads routinely drop "(feat. X)". The
   regex name split is turned off in that case. Album and year fetchers query the primary artist
   first and fall back to "A x B".
6. **Splitting existing joined names is always a user decision**, made in one batch review
   (`review_artist_splits()`), never automatic. A separator alone can't tell "Sw@da & Maxim" from
   "Simon & Garfunkel". Rejected names are remembered in `data/artist_split_ignore.json`.

## Consequences

- Any number of artists per song, with one query shape: `song_has_artist_id()`,
  `additional_artist_name_contains()`.
- Asking "songs by X" means checking two places (`songs.artist_id` OR the link table). That logic
  lives only in `song_artists.py`. Never inline it.
- Every artist merge must also move link rows (`reassign_additional_artist_links()`), or merged
  artists leave dangling or duplicate links behind. Both existing merge paths call it.
- Old joined artist rows coexist with the new model until the user splits them, so the
  heuristics for joined names (regex split, `truncate_at_word()`) stay in place for songs without
  credits.
- Reversing this after the joined names are split would mean re-joining names into artist rows,
  so treat it as a one-way door.

## Alternatives considered

- **One `feat_artist_id` column on `songs`.** Rejected: it holds only one extra artist, while ~60
  songs have three or more. The rest would stay as joined names, leaving two ways to store a collab
  side by side.
- **Comma-separated ids in one text column (e.g. `"13,21"`).** Rejected:
  - No foreign-key integrity, so deleting an artist leaves dead ids behind.
  - Searching needs `LIKE '%1%'`-style matching, which also hits 13, 21 and 100, and can't use an
    index.
  - Merging duplicates would have to parse and rewrite strings.

  `artists.synonyms` is comma-separated, but it is free text, not references to other rows.
- **Several columns (`feat1_artist_id`, `feat2_artist_id`, ...).** Rejected: no reasonable fixed
  count (real songs go up to 9 artists), every query has to OR across all of them, and raising the
  limit means a schema migration plus touching every one of those queries.
- **A pure link table holding *all* artists, with `songs.artist_id` removed.** Cleaner in
  principle, but rejected for now: it would mean rewriting every existing `song.artist` call site at
  once. Keeping the primary in `songs.artist_id` makes the change additive, so it could be rolled
  out in stages. Revisit if the two-place lookup becomes a maintenance burden.
