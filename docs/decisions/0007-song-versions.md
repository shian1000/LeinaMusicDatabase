# ADR-0007: Versions of a song (live, remix, acoustic...) as rows of their own, linked to the original

- Status: Accepted (owner, 2026-11-11)
- Date: 2026-11-11

## Context

A version of a song was a song row with the marker in its title: "Werewolf (live)", "Betonowy Las
(Ptaki remix)". Nothing tied it to the original, so:

- matching took a version for the original whenever the title was compared without brackets - a live
  file landed on the studio song, or a "(Live)" video on the studio row;
- an upload naming the version differently ("Dziwny jest ten świat (Bonus track)", the live take on
  *Zalewski śpiewa Niemena*) couldn't be told apart from the studio row by anything but its length;
- the phone couldn't show the other versions of a song.

On 2026-11-11 music.db held 167 titles with a version marker; only 14 had their original in the
database. Most versions are kept without the original.

## Decision

Modelled on covers (migrations 0020-0021): migration 0022 adds three columns to `songs`:

- `version_kind` - `constants.VERSION_KINDS` (live, remix, acoustic, edit, extended, demo,
  instrumental, other), NULL = not a version;
- `version_label` - the marker as shown ("Cid Rim Remix"), NULL = the kind's own label ("Live");
- `version_of` - the original song when music.db has it, else NULL; always the original itself.

A version stays a **row of its own** (own id, video, plays, album): two recordings are never merged
into one, and nothing that points at `songs.id` changes. The title loses the marker and the song is
shown "Title (Marker)" (`version_names.title_with_version()`, also inside `title_with_cover()`, so in
files too). Matching keeps versions apart in the rule every matcher applies
(`song_kinds.songs_for_entry()`): an entry whose title marks a version is matched only to versions of
that kind (and to titles still holding such a marker), any other entry never to a version.

A marker that only names a release of the same recording ("Radio Edit", "Album Version",
"Remastered", "Bonus track") is not a version.

## Consequences

- New songs get it when created (`create_song_entry()`, like a full album's marker); the stored ones
  go through Manage database -> "Find versions" (a list to tick, `version_checked` marker), and Song
  actions -> "Set version" marks one by hand.
- Merges move the versions to the kept song; a deleted original leaves them without one.
- The phone reads the columns ([handoff](../handoffs/song-versions-for-musicdatabaseapp.md)): it shows
  the marker, lists other versions, and keeps versions apart when it matches by name.
- Not done: the remixer as an artist (a `remix` role in `additional_song_artists`), and counting a
  version's plays with the original's in the stats.
