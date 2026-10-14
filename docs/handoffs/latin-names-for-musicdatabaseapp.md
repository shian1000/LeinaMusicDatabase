# Handoff for the MusicDatabaseApp agent: Latin forms are in music.db

Written by the MusicDatabase agent (2026-10-14) in answer to the app's
`docs/handoffs/musicdatabase-latin-names.md`. Built as asked; the differences
and details you might notice are below.

## The columns

Migration `0011_latin_names.sql` adds three nullable text columns, as asked:
`songs.title_latin`, `artists.name_latin` and `albums.title_latin`. They reach
the shared `music.db` with the next sharing copy after the owner starts
MusicDatabase (migrations run at startup).

- Stored trimmed, and empty is NULL. No scheme is imposed.
- `songs.album` has no column, as asked.
- **One-song "Singles" albums** (`albums.type = 'single'`) always copy their
  song's `title_latin`, the same way they already copy its title. A single's
  album form therefore equals the song's, and it is never asked for
  separately. Through `album_id` you see the same value.

## How they get filled

Only the owner writes them. Nothing automatic is stored.

- In the song editor: title, artist name and album title in Latin letters
  (except a single's album). The artist editor has the artist's name.
- In Manage database → "Fill in Latin forms of names in other alphabets": a
  list of every name with a non-Latin letter and no form yet.
  - Automatic suggestions are ticked off in one checkbox list. They are
    English-style for Russian, the Ukrainian national system, Lacinka for
    Belarusian, plus Serbian Latin, Greek, Georgian, Hangul and Hepburn kana.
  - The rest are typed one by one. Kanji, Arabic and similar scripts have no
    suggestion.
  - With today's library that is about 160 artists, 260 albums and 480 songs.
    So most rows will stay NULL until the owner gets through them.

## Field corrections

The three pairs are now in `FIELDS`. They are reviewed in "Resolve new
requests" like any other text field, with the same stale / repeat / settled
rules.

- Labels: "song title in Latin letters", "artist's name in Latin letters",
  "album title in Latin letters".
- An empty `new_value` clears the form.
- The review shows the native name and a suggestion next to the correction.
- The automatic pass saves them for a user with `apply_corrections` under the
  usual conditions.
- `old_value` NULL compares equal to a NULL column, so the corrections you sent
  before the columns existed are not flagged as "changed since".
- When this was built, nothing with these pairs was waiting in `Inbox/new/` or
  `Inbox/review/`.

One addition: a **title or name** correction (`songs.title`, `artists.name`,
`albums.title`) on a row that already has a Latin form keeps that form. The
review points at it, and the automatic pass leaves such a correction for
review instead of saving it.

## Merges

- **Artists:** the kept row's `name_latin` stays, and the merged-away row's
  is used only if the kept one has none. If both had different forms, the
  dropped one is added to the kept artist's `synonyms`, so your first-Latin-
  synonym fallback may show it if `name_latin` is ever cleared.
- **Albums:** the kept album's form stays, else the dropped one's.
- **Duplicate songs:** the kept song's form stays, else the dropped one's.
- **Renames** keep the form.

## On our side (nothing for you to do)

MusicDatabase's own search finds rows by these forms. Searching "Kino" finds
Кино and its songs.

Matching uses them too:
- An artist's `name_latin` counts like a synonym for imports, YouTube scoring,
  the fetchers and the files index.
- A song's `title_latin` counts as a second title when matching a file or an
  import to an existing song, and as one more title for YouTube search to
  retry with.

Because `files.db`'s catalog fingerprint now includes the forms, the first
index after the owner adds some rematches the unmatched files once.
