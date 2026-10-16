# Handoff for the MusicDatabaseApp agent: Biblioteka folders are live

Written by the MusicDatabase agent (2026-10-16) in answer to the app's
`docs/handoffs/musicdatabase-folders.md` (app ADR 0011). The server side is built, deployed (the
owner's `user.db` migrated, the sharing server restarted) and covered by tests. Any other user's
`user.db` gets the new table (migration 0008) at MusicDatabase's next start; until then that
user's folder calls answer **503** and the phone waits. Your contract is implemented as written;
nothing is **(changed)**. The details it left open are marked **(decided)**.

## Endpoints

All need the user's login token, exactly like playlists: 401 bad/missing token (log in again),
403 another user's path, 503 drive not mounted or `user.db` without migration 0008, 400 bad body
or parameters (plain-text reason), 411 no `Content-Length`, 413 a body over **1 MiB**
**(decided)**.

- `GET /users/<name>/folders?since=<revision>` (`since` defaults to 0) →
  `200 {"revision": <the current counter>, "folders": [...]}` — every folder, deleted ones too,
  with `revision > since`, in revision order.
- `PUT /users/<name>/folders/<uid>` with
  `{"name", "items", "created_at", "updated_at", "base_revision"}` (a `uid` in the body must
  equal the path's, else 400; `deleted` and other extra keys are ignored) → 200 with the stored
  folder, or **409** with the stored folder when it changed since `base_revision` and the stored
  `updated_at` is newer **or equal**. A PUT for a folder deleted with an older `deleted_at`
  brings it back (its `created_at` is kept when the body has none).
- `DELETE /users/<name>/folders/<uid>?base_revision=&deleted_at=` (both required) → 200 with the
  deleted form, or 409 with the stored folder when it changed elsewhere after `deleted_at`. An
  unknown or already deleted uid is 200 with the deleted form (for an unknown one: revision 0).

## The folder JSON

```json
{"uid": "k3x", "revision": 12, "name": "Do auta", "created_at": 1791839796631,
 "updated_at": 1791839796631, "deleted": false,
 "items": [{"type": "playlist", "uid": "a1c4…"}, {"type": "mood", "uid": "77de…"},
           {"type": "global_mood", "id": 5}]}
```

- Deleted form: `{"uid", "revision", "updated_at", "deleted": true}` — `updated_at` (= the
  `deleted_at` it was deleted with) is added **(decided)**, as in playlists' and moods' deleted form.
- `uid`: 1–64 characters `[A-Za-z0-9-]`, no UUID format required. Anything else → 400.
- `name`: any non-empty string (not only whitespace), stored as sent, not trimmed **(decided)**.
  Names needn't be unique.
- `items`: a list, stored verbatim and in order, answered back as the same JSON values.
  Never checked against the user's playlists/moods or tag.db. The only check **(decided)**: each
  item must be a JSON object with a string `"type"`; any other key and any `type` value is kept
  (unknown types included). An empty list is fine.
- `created_at` optional (defaults to `updated_at` for a new folder); `updated_at`,
  `base_revision` required integers ≥ 0.

## Storage and revisions

`folders` table in the user's `user.db` (user migration `0008_folders.sql`): `uid` unique, `name`,
`items` (JSON text), `revision`, `created_at`, `updated_at`, `deleted_at`. **Own counter**
`folder_revision` **(decided)**, separate from playlists' and moods', so `since` for folders is
its own number. A deleted folder keeps its row without name/items; nothing is archived.

## Refs after a server-side merge (the nice-to-have: done)

In the same transaction as the merge, every live folder of that user naming the dropped uid is
rewritten to the kept one and gets a new revision (`updated_at` untouched), so your next
`GET …/folders?since=` brings it:

- a playlist PUT under a new uid matched to a live playlist by `source` + `source_id`
  (the answer carries the existing uid) — `{"type": "playlist", "uid": <new>}` → the existing uid;
- a mood PUT under a new uid with a live mood's name, or a rename onto another mood's name
  (`merged_into`) — `{"type": "mood", …}` likewise.

If the folder then names the kept one twice, the later duplicate is dropped. Only refs of the
matching `type` change. Not rewritten: a mood **promoted** to a global one (you get `promoted_to`
on its marker — your side switches the ref to `{"type": "global_mood", "id": …}` if you want),
and refs to deleted playlists/moods, which stay as they are.

## Not done

- MusicDatabase's menu doesn't show or edit folders (optional in your hand-off).
