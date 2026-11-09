# Handoff for the MusicDatabaseApp agent: covers in music.db (songs.covered_artist_id)

Written by the MusicDatabase agent (2026-11-09). The owner asked for this on the
MusicDatabase side first. Please build the app side below, and write back in
`docs/handoffs/musicdatabase-cover-songs.md` if something doesn't fit.

## Correction, added after this handoff was first passed on

There is a second new column, which the first version of this handoff didn't mention:
`songs.covered_title` (migration `0021_song_covered_title.sql`, TEXT, NULL for most songs).

- A cover can now have `covered_artist_id` NULL and `covered_title` set: a traditional song
  with no artist to name. Such a song is still a cover. Treat a song as a cover when
  either column is set.
- Credit: `(<covered artist's name> cover)`; with no covered artist,
  `(<covered_title> cover)`: "Христина Соловій - Українська лють (Bella Ciao cover)".
- With an artist, `covered_title` isn't part of the credit. It's the original's title when
  it differs from the cover's ("Дарма" = American Football's "Never Meant"), which the app
  may show on a details screen.
- `artists` may hold a row that isn't a person: "Power Blade 2", a game whose soundtrack
  is covered. Show it like any other name.

Everything below already includes this.

## Why

A title used to carry the credit: "Creep (Radiohead cover)". The database now knows
it: the title is "Creep" and the song points at the covered artist.

## The schema

Migration `0020_song_covered_artist.sql`, in the shared and the public `music.db`:

- `songs.covered_artist_id`: NULL for most songs, else `artists.id` of the original
  artist. The performer stays `songs.artist_id`.
- `songs.covered_title` (migration `0021_song_covered_title.sql`): NULL for most songs, else
  the original song's title. It's set when a cover's title differs ("Дарма" = "Never Meant")
  or when no artist can be named: a traditional song has `covered_artist_id` NULL and only
  this column.

Song ids didn't change, so playlists and saved songs are unaffected. For the songs
already marked the title lost its credit, and a song titled "(Cover by X)" also moved
to X as its `artist_id`. 41 songs are marked so far and more will follow. The column
reaches phones with the next copy for sharing and the next public release.

## What the app should do

- Show such a song as `Artist - Title (Covered cover)`, wherever it shows artist and
  title ("Hannah Rue - Crazy in Love (Beyoncé cover)").
- `artists` can now hold an artist with no songs at all, only covered ones. Leave
  such a row out of artist lists, or show it with its covers.
- With no covered artist, show `covered_title` in the credit: "Христина Соловій - Українська
  лють (Bella Ciao cover)". With an artist, the credit shows the artist; `covered_title` is
  extra information the app may show on a details screen.
- Use the covered artist's `artists.name` (with `name_latin` where the app shows Latin
  forms). Don't write the credit into `songs.title` or send it as a title correction:
  the title is meant to stay without it.
- When the app searches for a missing YouTube link, add the word "cover" and the
  covered artist's name, or it may find the original.
- When matching a played video by artist and title, a video titled "Creep (Radiohead
  cover)" is the row titled "Creep": drop the credit before comparing.

Before the app reads the column it shows those rows as "Artist - Title". Nothing
breaks, but the credit is no longer visible there.
