# Handoff for the MusicDatabaseApp agent: listening stats (plays) are live

Written by the MusicDatabase agent (2026-10-16) in answer to the app's
`docs/handoffs/musicdatabase-listening-stats.md` (app ADR 0012). The server side is built, deployed
(the owner's `user.db` migrated, the sharing server restarted) and covered by tests. Any other
user's `user.db` gets the new table (migration 0009) at MusicDatabase's next start; until then that
user's listens calls answer **503** and the phone keeps its plays. Your contract is implemented as
written; nothing is **(changed)**. The details it left open are marked **(decided)**.

## Endpoints

All need the user's login token, exactly like playlists and folders: 401 bad/missing token (log in
again), 403 another user's path, 503 drive not mounted or `user.db` without migration 0009, 400 bad
body or parameters (plain-text reason), 411 no `Content-Length`, 413 a body over **1 MiB**
**(decided)**. Any other method on the path (PUT, DELETE) or a sub-path (`…/listens/<uid>`) → 404.

- `GET /users/<name>/listens?since=<revision>&limit=<n>` →
  `200 {"revision": <r>, "listens": [...], "more": <bool>}` — plays with `revision > since`, in
  revision order, at most `limit`. With `more: true`, `r` is the last listed play's revision (ask
  again with `since=r`); otherwise `r` is the current counter. `since` defaults to 0; `limit`
  defaults to **1000** and is capped at **1000** **(decided)** — a larger one is silently lowered,
  `limit=0` or a non-integer is 400. Exactly `limit` plays left gives `more: false`.
- `POST /users/<name>/listens` with `{"listens": [<plays>]}` → `200 {"revision": <the current counter>}`.
  An empty list is fine (answers the counter). No cap on the number of plays per request besides the
  1 MiB body **(decided)**. A batch is all or nothing: one bad play → 400 naming it
  (`listens[3]: listened_ms: expected an integer >= 0`), nothing stored.

## Storing

- A new `uid` is added under the next revision.
- A known `uid` is updated — new revision — **only when the sent `listened_ms` is larger**; then
  `duration_ms` is filled in if it was null (a known duration is never overwritten). Equal or
  shorter: nothing changes, no new revision. Nothing else about a play ever changes after the
  first POST (title, song id, extra keys, …), as you asked. The same uid twice in one batch is
  applied in order.
- No deletions.

## The play JSON

Served back exactly as you sent it, plus `revision`:

```json
{"uid": "3f2b…", "started_at": 1791839796631, "listened_ms": 183400, "duration_ms": 214000,
 "title": "Gruppa krovi", "artist": "Kino", "album": "Gruppa krovi", "song_id": 1234,
 "youtube_video_id": "abc123", "spotify_track_id": null, "source": "youtube",
 "playlist_uid": "a1c4…", "playlist_name": "Do auta",
 "mood_uid": null, "global_mood_id": 5, "mood_name": "Do biegania", "revision": 17}
```

Checks **(decided)** — everything else is stored as given:

- `uid`: 1–64 characters `[A-Za-z0-9-]`, required.
- `started_at`: required integer ≥ 0. `listened_ms`: required integer ≥ 0. `duration_ms`: integer
  ≥ 0 or null/missing. (JSON booleans and floats such as `1.5` are not integers.)
- `title`: required string (an empty string is accepted, so one odd track can't block a batch
  forever). `artist`, `album`, `youtube_video_id`, `spotify_track_id`, `source`, `playlist_uid`,
  `playlist_name`, `mood_uid`, `mood_name`: string or null/missing.
- `song_id`, `global_mood_id`: integer or null/missing, any value — never checked against
  music.db / tag.db.
- A missing optional key comes back as `null`: every column above is always in the answer.
- **Unknown keys are kept** (an `extra` JSON column) and served back as sent, whatever their
  value. A `revision` key in a POSTed play is ignored, not stored. Unknown `source` values are kept.

## Song ids

`song_id` is stored as sent and **not remapped** when MusicDatabase later merges or deletes that
song **(decided)**: remapping would change plays behind the phones' backs (a new revision, or two
phones disagreeing). For stats across a merge, resolve old ids through the shared music.db's
`song_id_changes` (`old_song_id` → `new_song_id`, NULL = deleted), as you do for anything else
holding song ids.

## Storage and revisions

`listens` table in the user's `user.db` (user migration `0009_listens.sql`): `uid` unique, one
column per field above, `extra`, `revision`; indexes on `revision` and `started_at`. **Own
counter** `listen_revision`, separate from playlists', moods' and folders', so `since` for listens
is its own number. Private: no review, no permission.

## Not done

- MusicDatabase's menu doesn't show plays (no count or top 10 under Users yet — optional in your
  hand-off; the owner can ask for it later).
