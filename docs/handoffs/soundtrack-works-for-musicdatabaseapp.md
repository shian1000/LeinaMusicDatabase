# Handoff for the MusicDatabaseApp agent: soundtrack works in music.db (soundtrack_works)

Written by the MusicDatabase agent (2026-11-18). The owner asked for this on the
MusicDatabase side first. Please build the app side below, and write back in
`docs/handoffs/musicdatabase-soundtrack-works.md` if something doesn't fit.

## Why

YouTube uploads and files often credit a song to the film, game or anime it comes from
instead of the singer, with nothing such as "OST" in the name saying so. A playlist added
from Leina Radio held "Nana - Kuroi Namida". In music.db the song is "土屋アンナ - 黒い涙"
(Latin form of the title "Kuroi Namida"), on the album "Nana OST". MusicDatabase's matcher
found the artist "Nana", saw no song of that title there and the request added the song a
second time. The app has the same blind spot when it matches a played video or a phone file
by artist and title.

## The schema

Migration `0025_soundtrack_works.sql`, in the shared and the public `music.db`:

- `soundtrack_works`: `id`, `name` (unique, "Nana"), `aliases` (other spellings,
  comma-separated, may be NULL). The owner keeps the list (Manage database ->
  "Soundtrack works"). It holds only the works the catalog can't tell by itself.

## When a name is a work

An artist name, normalized the way the app already compares names, is a work when it is:

1. the `name` or one of the `aliases` of a `soundtrack_works` row, as a whole name - "Nana"
   is the anime, "Nana Mouskouri" isn't;
2. a work the catalog has an album of: an album title ending in a soundtrack word
   ("Cave Story OST", "Tekken 5 Soundtrack") gives the work before that word ("Cave Story").
   Only works of **two words or more** count this way, so "Rio OST" doesn't make every
   "Rio ..." artist a film;
3. itself ending in a soundtrack word ("Tekken 5 OST" -> "Tekken 5").

MusicDatabase's reading of the soundtrack words is `utils/common/soundtrack_names.py`
(`soundtrack_work()`): at the end of the name, optionally bracketed, "OST", "O.S.T.",
"Soundtrack(s)", optionally after "Original", "Official", "Motion Picture", "Video Game",
"Game", "Anime", "Film", "Movie", "TV" or "Series".

## What the app should do

When matching a video or a file to a song by artist and title finds nothing under the artist
(or no such artist), and the artist name is a work:

- take the songs of **any** artist whose title matches (the title, `title_latin` and
  `other_titles`, as the app already compares titles);
- keep those whose `album` or whose artists' names or synonyms contain the work's name (or one
  of its aliases) as **whole words** - "Nana" in "Nana OST" or "Anna Tsuchiya inspi' NANA
  (Black Stones)";
- take the song only when exactly **one** is left. None or several: no match, as before (the
  song request goes to MusicDatabase, which asks the owner).

Keep the app's existing rules on top of this: versions, covers, projects and full albums are
told apart exactly as for any other match.

MusicDatabase's own version: `LocalSongMatcher.match_work()` in
`src/utils/discoveries/import_engine.py`, with `matching_works()` / `credited_work()` in
`src/utils/database/soundtrack_albums.py`; tests in `tests/test_soundtrack_albums.py`.

Before the app reads the table nothing breaks: such a song is just not recognised, and the
app sends a song request for it as it does today.
