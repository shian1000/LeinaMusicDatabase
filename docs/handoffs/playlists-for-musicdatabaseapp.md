# Handoff for the MusicDatabaseApp agent: playlist sync is live

Written by the MusicDatabase agent (2026-10-13) in answer to the app's
`docs/handoffs/musicdatabase-playlists.md`. The server side is built, deployed and
tested against the real server; the phone can start syncing. The owner's decisions
1–7 are implemented as written. Differences and details the handoff left open are
marked **(changed)** or **(decided)**.

## Endpoints

Same server, port and addresses as the database download (the server's LAN and
Tailscale addresses, port 8002), plain HTTP, JSON bodies with
`Content-Length` (no chunked). Every call needs `Authorization: Bearer <token>`
from `POST /login`, and `<name>` must be the token's user.

| Call | Success | Notes |
|---|---|---|
| `GET /users/<name>/playlists?since=<revision>` | 200 `{"revision": <counter>, "playlists": [...]}` | `since` defaults to 0; every playlist with `revision > since`, deleted ones included, ordered by revision |
| `PUT /users/<name>/playlists/<uid>` | 200 the stored playlist | body below |
| `DELETE /users/<name>/playlists/<uid>?base_revision=<n>&deleted_at=<unix ms>` | 200 the deleted form | both parameters required |

| Code | Meaning | What the phone should do |
|---|---|---|
| 200 | applied (or, for DELETE, already gone / never known) | save exactly the answer (new `revision`, maybe another `uid`, resolved song ids) |
| 409 | the stored playlist won the conflict; the body is that playlist | take it, including its `uid` (see "Same import on two phones") |
| 400 | bad JSON, missing/mistyped field, bad uid, bad parameter | bug on the phone — keep the change, don't retry blindly |
| 401 | no token, malformed, unknown or revoked | log in again |
| 403 | the token belongs to another user than `<name>` | bug on the phone |
| 411 | no `Content-Length` | send with a length |
| 413 | body over 8 MiB **(decided)** | shouldn't happen at 5000 tracks (~1.5 MiB) |
| 503 | data drive not mounted, that `user.db` hasn't got the migration yet, or the file stayed locked for 15 s | retry later |
| 404 | any other path or method (e.g. `GET …/playlists/<uid>`) | — |

## A playlist in JSON

Exactly as in your handoff. The answer always has all fields:

```json
{"uid": "…", "revision": 17, "name": "0. Fixed", "source": "folder", "source_id": "/media/…/0. Fixed",
 "source_owner": null, "created_at": 1791839796631, "updated_at": 1791839796631, "deleted": false,
 "tracks": [{"song_id": 1234, "title": "…", "artist": "…", "youtube_video_id": "…",
             "spotify_track_id": null, "youtube_removed": false, "duration_s": 215}]}
```

Deleted: `{"uid", "revision", "updated_at", "deleted": true}`. `owner` (the username) is
never sent. `source` is free text; the known values are `folder`, `youtube`, `spotify`,
`spotifyAlbum`.

**PUT body (decided):** the same fields without `revision`/`deleted`, plus
`base_revision` (required, integer ≥ 0). Required: `name` (non-empty string),
`updated_at` (integer ≥ 0), `tracks` (list), each track's `title` (string). Optional:
`source`, `source_id`, `source_owner` (string or null), `created_at` (integer; if
missing, a new playlist takes `updated_at`, an existing one keeps its own),
`song_id` (integer ≥ 1 or null), `artist`, `youtube_video_id`, `spotify_track_id`
(string or null), `youtube_removed` (true/false, default false), `duration_s`
(integer or null). A `uid` in the body is optional but must equal the one in the
path. A uid is 8–64 characters of `0-9 A-Z a-z -` (a UUID fits).

## Rules as built

- **Conflicts:** `base_revision` equal to the stored revision → applied, even if its
  `updated_at` is older. Otherwise applied only if `updated_at` (for DELETE,
  `deleted_at`) is later than the stored `updated_at`; else 409. A later edit brings a
  deleted playlist back. The server stores your `updated_at`, never its own clock.
- **Resend after a lost answer:** gets a 409 whose body matches what you sent.
- **Same import on two phones:** a PUT of an unknown uid whose `source` + `source_id`
  (both non-null) match a live playlist goes to that playlist. **(changed)** The
  conflict rule applies to it like any stale change (your `base_revision` 0 is never
  its revision): a newer `updated_at` replaces it (200), an older one gets 409. **Either
  way the answer carries the existing uid; adopt it from a 409 too.** Deleted
  playlists are never matched this way.
- **Revisions you didn't cause:** a song merge or deletion in MusicDatabase that
  changes a track's `song_id` gives the playlist a new revision with `updated_at`
  unchanged. Take it from the next GET like any other change.
- **Song ids:** before storing, every incoming `song_id` goes through the shared
  `music.db`'s `song_id_changes` (merged → the kept song, following chains; deleted →
  null, title/artist kept). **(decided)** An id that is neither a song in that copy nor
  in the log also becomes null; no `music.db` could have given it to you. To close
  the gap from that copy lagging by up to a day, the playlist remembers the last log
  entry it was checked against. MusicDatabase's next `apply_song_id_changes()`
  replays the rest for that playlist only, and gives it a new revision if anything
  changed.
- **DELETE:** unknown uid → 200 `{"uid", "revision": 0, "updated_at": <deleted_at>,
  "deleted": true}`; already deleted → 200 with the stored deleted form. On success the
  whole playlist is first written to `Users/<name>/archive/playlists/<uid>.<YYMMDD-HHMM>.json`
  (the deletion time, `_2` etc. if taken, never overwritten), then its tracks are
  dropped and the row stays as the marker.
- **The menu:** "Create playlist from folder" makes a playlist like a phone would (new
  uid, next revision; `source` `folder`). Importing the same folder twice in the menu
  makes two playlists; the source merge is only for PUTs. Users → "Show playlists" now
  also has "Delete playlist", archived the same way. There's no rename in the menu yet.

## State on the real server

The owner's `user.db` is migrated: "0. Fixed" has a uid and revision 1, and the
counter is 1. A curl round trip (GET, PUT, stale PUT → 409, no token → 401, another
user → 403, DELETE + archive file, GET `since=1`) passed on the LAN address with a
temporary token. The test playlist, its archive file and the token were removed
afterwards. A new user's `user.db` gets the migration at creation. Others get it at the
next MusicDatabase startup with the data drive mounted, and until then the server answers 503 for
them.

Code: `src/utils/database/playlist_sync.py` (rules, shared by the server and the menu),
`sharing_server.py` (endpoints), `migrations/user/0004_playlist_sync.sql`. Runbook:
`docs/runbooks/database.md` → "Playlists on the phone". Tests: `tests/test_playlist_sync.py`,
`tests/test_database_sharing.py` (playlists section), `tests/test_song_references.py`.
