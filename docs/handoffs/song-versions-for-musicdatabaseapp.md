# Handoff for the MusicDatabaseApp agent: versions of a song (songs.version_kind)

Written by the MusicDatabase agent (2026-11-11), on the owner's request
([ADR-0007](../decisions/0007-song-versions.md)). Please build the app side below, and write back in
`docs/handoffs/musicdatabase-song-versions.md` if something doesn't fit.

## Why

A live recording or a remix used to be a song row with the marker in its title ("Werewolf (live)"),
tied to nothing. A playlist showed what that costs: "Dziwny jest ten świat (Bonus track)" is the
live take, a different recording from the studio row #3994, and only the lengths told them apart.

## The schema

Migration `0022_song_version.sql`, in the shared and the public `music.db`, three new columns on
`songs`, NULL for most songs:

- `version_kind` (TEXT): `live`, `remix`, `acoustic`, `edit`, `extended`, `demo`, `instrumental` or
  `other`. NULL = not a version. Treat an unknown value as `other`.
- `version_label` (TEXT): the marker as shown, "Cid Rim Remix", "na żywo, akustycznie". NULL = the
  kind's own label: Live, Remix, Acoustic, Edit, Extended, Demo, Instrumental, Version.
- `version_of` (INTEGER): `songs.id` of the original, when music.db has it; always the original
  itself, never another version. Most versions have none (153 of the first 167).

A version is a song row of its own: its own id, YouTube link, album and plays. Song ids didn't change.
The title is stored **without** the marker: "Boję się" with `version_kind` `live`.

## What the app should do

- Show a version as `Title (Marker)` wherever it shows a title ("Boję się (Live)"), through
  `shownTitle` like a cover's credit; a cover version gets both: "Creep (Live) (Radiohead cover)".
  Don't write the marker into `songs.title` or send it as a title correction.
- Informacje o utworze: "Inne wersje" - the original and its versions (songs with the same
  `version_of`, plus the row it points at), when there are any.
- Matching by name (`SongCatalog.findExact` / `findByName`): a title whose marker names a version
  ("Boję się (Live)", "Recover (Cid Rim Remix)") matches only a version of that kind, the same
  marker first; a title without one never matches a version. A marker naming only a release
  ("Radio Edit", "Album Version", "Remastered", "Bonus track") is no version. MusicDatabase's reading:
  `src/utils/common/version_names.py` (`read_version()`).
- When looking for a missing YouTube link of a version, add its marker to the query, or the search
  finds the original.
- Song requests: a played video titled "Song (Live)" goes as it is; MusicDatabase marks the version
  when it adds the song.
- A song's radio, Odkrywaj and random picks may leave versions out when the original is in the list
  (your call).

Before the app reads the columns it shows such rows without the marker ("Boję się" twice). Nothing
breaks, but a live take looks like the studio song.
