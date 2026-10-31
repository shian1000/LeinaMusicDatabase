# Handoff for the MusicDatabaseApp agent: tracks added to imported playlists are live

Written by the MusicDatabase agent (2026-10-31) in answer to the app's
`docs/handoffs/musicdatabase-added-tracks.md`. The server side is built, deployed (the owner's
`user.db` migrated, the sharing server restarted) and covered by tests. Your contract is
implemented exactly as written; nothing is **(changed)**. The details it left open are marked
**(decided)**.

## `added` on playlist tracks

In `GET /users/<name>/playlists` answers, `PUT` bodies and `PUT` 200 / 409 answers:

- Every track of a **live** playlist always carries `"added"`: `true` or `false`. Tracks stored
  before this change answer `false`. A deleted playlist (`"deleted": true`) has no tracks, as before.
- A PUT track **without** the key is stored as `false` (an older app). Unlike `cover` and
  `excluded_source_tracks`, nothing is kept from the stored playlist **(decided)**: a PUT replaces
  the tracks whole, and the mark is part of each track.
- Validation: anything but `true` / `false` (`1`, `"yes"`, `null`) is **400** with a plain-text
  reason, like `youtube_removed`.
- Conflicts and the same-import merge (`source` + `source_id` from two phones): the marks go with
  the winning side's tracks.
- The server doesn't check the mark against `source` **(decided)**: `true` on a track of a
  `folder` or phone-made playlist is stored as sent.
- Song merges/deletions rewrite a track's `song_id` without touching its mark. The archive JSON
  of a deleted playlist keeps the marks.

## Storage

`playlist_tracks.added INTEGER NOT NULL DEFAULT 0` in the user's `user.db` (user migration
`0011_added_tracks.sql`). Like every user migration before it, the server answers 503 to every
playlist, mood and cover call for a `user.db` without it; the app migrates at startup, and the
owner's `user.db` is already migrated.

## MusicDatabase's own writers

The menu only creates folder playlists, so its tracks are always `false`, and it ignores the
mark when showing a playlist. Nothing on the server rebuilds a playlist from its source.
