# Handoff for the MusicDatabaseApp agent: projects in music.db (songs.project_id)

Written by the MusicDatabase agent (2026-11-06). The owner asked for this on the
MusicDatabase side first. Please build the app side below, and write back in
`docs/handoffs/musicdatabase-projects.md` if something doesn't fit.

## Why

Some recordings were made for a series or a compilation: NPR's Tiny Desk, triple j's
Like a Version, Kaunas Acoustics. The artist is the real artist and the title no
longer carries the project's name, so two rows can now read the same, "Wolf Alice -
Boys": the studio song and the project's recording. They are different recordings.

## The schema

Migration `0019_song_projects.sql`, in the shared and the public `music.db`:

- `projects`: `id`, `name` (unique), `aliases`, `channels`. Show `name`; the other two
  are comma-separated and only for MusicDatabase's own matching.
- `songs.project_id`: NULL for most songs, else `projects.id`.

## What the app should do

- Show such a song as `Project: Artist - Title`, wherever it shows artist and title
  ("Tiny Desk: yeule - Tiny Desk Concert").
- When matching a played video or file to a song by artist and title, don't take a
  row with a `project_id` for a video that doesn't name that project, or the other
  way round. Without this the studio song and the recording get mixed up.
- When the app searches for a missing YouTube link, add the project's name to the
  search.
- A project's whole set is an ordinary `kind = 'full_album'` row, so it's already left
  out of the radio. Its title ("Tiny Desk Concert") is the same for many artists: don't
  treat rows as one by title alone. Its `album` is often empty.

Before the app reads the column it shows those rows as "Artist - Title". Nothing
breaks, but the two versions look the same.
