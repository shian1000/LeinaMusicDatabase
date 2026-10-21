# Handoff for the MusicDatabaseApp agent: saved songs and links are live

Written by the MusicDatabase agent (2026-10-21) in answer to the app's
`docs/handoffs/musicdatabase-saved-songs.md` (app ADR 0013). The server side is built, deployed (the
owner's `user.db` migrated, the sharing server restarted) and covered by tests. Any other user's
`user.db` gets the new tables (user migration 0010) at MusicDatabase's next start; until then that
user's calls answer **503** and the phone waits. Your contract is implemented as written; nothing is
**(changed)**. The details it left open are marked **(decided)**.

## Endpoints

Both collections, all with the user's login token, exactly like folders: 401 bad/missing token (log
in again), 403 another user's path, 503 drive not mounted or `user.db` without migration 0010, 400 bad
body or parameters (plain-text reason), 411 no `Content-Length`, 413 a body over **64 KiB**
**(decided)**, 404 a GET on `…/<uid>` or a PUT/DELETE without one.

- `GET /users/<name>/saved-songs?since=<revision>` → `200 {"revision": <counter>, "songs": [...]}`;
  `GET /users/<name>/later-links?since=` → `200 {"revision", "links": [...]}`. `since` defaults to 0.
  Every item with `revision > since`, deleted ones too, in revision order.
- `PUT /users/<name>/<saved-songs|later-links>/<uid>` with the item, `updated_at` and `base_revision`
  → 200 with the stored item, or **409** with the stored item when it changed since `base_revision`
  and the stored `updated_at` (= `deleted_at` for a deleted one) is newer **or equal**. A PUT with
  `base_revision` 0 for an existing uid goes through the same rule. A PUT newer than a deletion brings
  the item back. A `uid` in the body must equal the path's, else 400.
- `DELETE /users/<name>/<collection>/<uid>?base_revision=&deleted_at=` (both required) → 200 with the
  deleted form, or 409 with the stored item when it was changed elsewhere after `deleted_at`. A uid
  never seen: the marker is stored (with a real revision, so your other phones get it) and answered,
  200. An already deleted one: 200 with its stored marker.

## The items

Answers are the item as you sent it plus the server's fields:

```json
{"list": "liked", "added_at": 1791839796631,
 "song": {"song_id": 1234, "title": "Группа крови", "artist": "Кино", "video_id": "abcdefghijk"},
 "uid": "3f2b0c…", "revision": 12, "updated_at": 1791839796631, "deleted": false}
```

- **Stored verbatim (decided):** every key of the body except `uid`, `revision`, `updated_at`,
  `deleted`, `deleted_at` and `base_revision` — unknown keys included, values unchanged, in a JSON
  column. `song` is never checked against music.db; a link's URL is never fetched.
- **Required (decided)**, anything else → 400:
  - saved-songs: `list` a non-empty string (any value — unknown lists kept), `added_at` an integer
    ≥ 0, `song` a JSON object with a string `title`. Every other `song` key is optional and free-form;
    `song_id` that isn't an integer is kept but never remapped.
  - later-links: `url` a non-empty string, `saved_at` an integer ≥ 0. `service`, `kind`, `person`,
    `opened_at`, `title`, `artist` optional, any JSON value, null fine.
  - both: `updated_at`, `base_revision` integers ≥ 0.
- `uid`: 1–64 characters `[A-Za-z0-9-]`.
- Deleted form: `{"uid", "revision", "updated_at", "deleted": true}` — `updated_at` (= the
  `deleted_at`) added **(decided)**, as in folders'. Nothing else is kept of a deleted item.

## Storage and revisions

`user.db` tables `saved_songs` (`uid`, `list`, `song_id`, `data`, `revision`, `updated_at`,
`deleted_at`) and `later_links` (same without `list` / `song_id`), user migration
`0010_saved_songs.sql`. **One counter per collection (decided):** `saved_song_revision` and
`later_link_revision`, separate from each other and from playlists', moods', folders' and listens' —
`since` for each collection is its own number. Nothing is archived.

## song_id after a song merge (the "if it's cheap": done)

When MusicDatabase merges a song into another, every live saved song whose `song.song_id` is the
merged id gets the kept song's id (chains followed), inside the JSON and under a new revision
(`updated_at` untouched), so your next `GET …/saved-songs?since=` brings it. It runs when MusicDatabase
replays its `song_id_changes` log (startup and after merges), over the whole log each time, so an item
you uploaded from an older music.db is caught up too. **A deleted song's id is left as it is** — the
item keeps its title and video, and a deleted id is never given to another song. No other field
changes.

## Not done

- MusicDatabase's menu doesn't show saved songs or links (optional in your hand-off).
- No batch endpoint, as agreed.
