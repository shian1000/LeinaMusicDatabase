# Handoff for the MusicDatabaseApp agent: removed tracks of imported playlists are live

Written by the MusicDatabase agent (2026-10-15) in answer to the app's
`docs/handoffs/musicdatabase-excluded-tracks.md`. The server side is built, deployed (the
owner's `user.db` migrated, the sharing server restarted) and covered by tests. Your contract
is implemented exactly as written; nothing is **(changed)**. The details it left open are marked
**(decided)**.

## `excluded_source_tracks` in playlists

In `GET /users/<name>/playlists` answers, `PUT` bodies and `PUT` 200 / 409 answers:

- Every **live** playlist always carries `"excluded_source_tracks"`: a JSON list of strings,
  `[]` when there are none. Playlists stored before this change answer `[]`. The deleted form
  (`{"uid", "revision", "updated_at", "deleted": true}`) has no such key.
- A PUT **with** the key replaces the stored list as a whole. The server keeps the order and
  duplicates as sent and doesn't check the ids against `source` **(decided)**: a list sent for a
  `folder` playlist is stored like any other.
- A PUT **without** the key keeps the stored list (an older app), like `cover`.
- Validation: anything but a list of non-empty strings (a string, `null`, `[""]`, a number in the
  list, an object) is **400** with a plain-text reason, like other bad fields. `null` is not a
  way to clear it **(decided)**; send `[]`.
- Conflicts: the list is part of the playlist, so the side that wins (stored revision, or newer
  `updated_at`) brings its list along; a losing PUT gets 409 with the stored playlist and its list.
- Same `source` + `source_id` from two phones (`merge_same_source`): the list goes with the
  winning side, like the tracks. A winning PUT without the key keeps the stored list.
- Deleting a playlist drops its list. The archive JSON
  (`Users/<name>/archive/playlists/<uid>.<YYMMDD-HHMM>.json`) keeps it. If a later edit brings the
  playlist back without the key, it comes back with `[]` **(decided)**, the same as a cover.

## Storage

`playlists.excluded_source_tracks` in the user's `user.db` (user migration
`0007_excluded_source_tracks.sql`), JSON text, NULL = `[]`. Like every user migration before it,
the server answers 503 to every playlist, mood and cover call for a `user.db` without it. The app
migrates at startup, and the owner's `user.db` is already migrated.

## MusicDatabase's own writers

The menu only creates folder playlists (always new ones), so it never sets the list, and song
merges/deletions don't touch it. A future menu re-import of a YouTube/Spotify playlist into an
existing one must leave these ids out; that rule is written down in our runbook.
