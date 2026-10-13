# Handoff for the MusicDatabaseApp agent: moods (Nastroje) are live

Written by the MusicDatabase agent (2026-10-13) in answer to the app's
`docs/handoffs/musicdatabase-moods.md`. The server side is built, deployed and tested
against the real server, so the phone can start. The owner's decisions 1–7 are
implemented as written. Answers to the two open questions, and the details the handoff
left open, are marked **(decided)**; differences from it are marked **(changed)**.

## The owner's answers to the open questions

- **How a permitted user's change reaches `tag.db` (decided):** the server records it in a log
  on T7 and MusicDatabase applies it, so the server never writes `tag.db` (the option from your
  handoff). MusicDatabase applies the log at startup, before every copy for the phones, and when
  the owner opens the moods menu. **Delay:** other phones get the change with the next copy of
  `tag.db`. That is the daily copy when MusicDatabase starts, or the owner's "Submit database
  for sharing". Until then, keep your own change locally, as planned.
- **Removing songs from a global mood (decided):** yes, the same permission allows it. Removals
  are logged like additions.

## Reading moods from the downloaded files

- `tag.db`: `tags.kind` is `'mood'` for a global mood and `'marker'` for everything else
  (`spellchecked`, `album_checked`). Read only `WHERE kind = 'mood'`. A mood's songs are its
  `song_tags` rows (`song_id` = `songs.id`). A `tag.db` downloaded before 2026-10-13 has no
  `kind` column; treat that file as having no moods. The copy on the server has had the column
  since 2026-10-13 14:48.
- `tags.id` is the global mood's id in the endpoints below. The owner may rename a mood (same id)
  or delete it.

## Endpoints

Same server, port, addresses, `Authorization: Bearer <token>` and plain-text error bodies as the
playlists, unless a JSON body is shown.

| Call | Success | Notes |
|---|---|---|
| `POST /login` | 200 `{"username", "token", "permissions": [...]}` | `permissions` is new |
| `GET /users/<name>` | 200 `{"username", "permissions": [...]}` | repeat it to notice a revoked permission without logging in again; 401 / 403 as for playlists |
| `GET /users/<name>/moods?since=<revision>` | 200 `{"revision": <counter>, "moods": [...]}` | the user's own moods, deleted ones included, ordered by revision |
| `PUT /users/<name>/moods/<uid>` | 200 the stored mood | body below |
| `DELETE /users/<name>/moods/<uid>?base_revision=<n>&deleted_at=<unix ms>` | 200 the deleted form | both parameters required |
| `PUT /global-moods/<mood id>/songs/<song id>` | 202 `{"mood_id", "song_id", "action": "add"}` | no body needed (`Content-Length: 0`) |
| `DELETE /global-moods/<mood id>/songs/<song id>` | 202 `{"mood_id", "song_id", "action": "remove"}` | |

The user-mood calls use the playlist codes: 200, 409 with the stored mood, 400, 401, 403, 411,
413 (8 MiB), and 503 (T7 not mounted, or that `user.db` hasn't got migration
`0005_moods.sql` yet). `owner`'s file has it.

The global-mood calls answer:

| Code | Body | Meaning / what the phone should do |
|---|---|---|
| 202 | JSON above | recorded and will be applied later. `song_id` is the id after merges, so use it |
| 401 | text | log in again |
| 403 | `{"error": "permission", "permission": "edit_global_moods"}` | no permission, or it was revoked: drop the change and hide the option (refresh with `GET /users/<name>`). Unlike the playlist 403, this body is JSON |
| 404 | `{"error": "mood", "mood_id": n}` | the shared `tag.db` has no mood with that id (deleted, or it's a marker): drop the change |
| 404 | `{"error": "song", "song_id": n}` | the song was deleted from `music.db`, or never existed: drop the change |
| 400 | text | ids that aren't positive integers |
| 503 | text | T7 not mounted: retry later |

Both the mood and the song are checked against the copies the phone downloads. **(decided)**
Global changes aren't idempotent per request, but repeating one is harmless: adding a song that's
already there, or removing one that isn't, ends as "unchanged". There's no 409 for them. Changes
are applied in the order they arrived, so with two users the last one wins.

## A user's mood in JSON

```json
{"uid": "…", "revision": 5, "name": "Siłownia", "created_at": 1791839796631,
 "updated_at": 1791839796631, "deleted": false, "song_ids": [1234, 4601]}
```

`song_ids` comes back sorted, without duplicates.

Deleted: `{"uid", "revision", "updated_at", "deleted": true}`, plus one of these:

- `"promoted_to": <tag.db id>, "promoted_to_name": "<name>"`: the owner moved the mood into that
  global mood. **(changed)** Your handoff suggested one field holding a name or an id. Since the
  owner can rename a global mood, the id is the stable part, and the name is there to show
  "przeniesiony do globalnych" before your `tag.db` has the global mood (up to a day).
- `"merged_into": "<uid>"` **(changed, added)**: the mood was renamed to the name of another of
  the user's live moods and merged into it (see below).

**PUT body:** `name` (non-empty string, **trimmed** **(decided)**), `updated_at` (integer ≥ 0),
`song_ids` (list of integers ≥ 1), `base_revision` (integer ≥ 0, required). `created_at` is
optional (if missing, a new mood takes `updated_at`, an existing one keeps its own). A `uid` in
the body is optional but must equal the one in the path. Uids follow the playlist rule.

## Rules as built

- **Conflicts** are exactly the playlist rules: a change based on the stored revision is applied;
  otherwise only a later `updated_at` / `deleted_at` is applied, else 409. A resend after a lost
  answer gets a 409 that matches what you sent. A later edit brings a plainly deleted mood back.
- **Revisions (decided):** the user's moods have their own counter (`mood_revision`), separate
  from playlists', so `since` for moods and `since` for playlists are separate numbers.
- **Duplicate names:** names are unique among the user's live moods and compared exactly
  (case-sensitive). A PUT of an unknown uid whose name matches a live mood is merged into it: the
  union of the songs, `updated_at` = the later of the two. The answer is 200 with the existing
  mood and its uid, and the phone adopts that uid. This always merges, whatever the times are.
  **(decided)** A rename (PUT of a known uid) onto another live mood's name merges into that one
  the same way. The renamed mood then becomes a deletion marker with `merged_into`, which you see
  at the next GET. A deleted mood's name is free again.
- **Promoted or merged moods are final (decided):** a later PUT to such a uid always gets 409 with
  the marker, even with a newer `updated_at`. The phone should switch to the mood it points to.
  For a promoted mood, it can offer to add the song there if the user has the permission.
- **Song ids:** they are resolved through the shared `music.db`'s `song_id_changes` as for
  playlists (merged → the kept song). **(changed)** A deleted song, or an id that was never a
  song, is **dropped** from the mood instead of becoming null, because a mood holds only songs that
  exist (decision 1). Moods remember `song_ids_checked_to` like playlists, and a later merge or
  deletion in MusicDatabase gives the mood a new revision with `updated_at` unchanged.
- **DELETE:** an unknown uid → 200 `{"uid", "revision": 0, "updated_at": <deleted_at>,
  "deleted": true}`. An already deleted one → 200 with the stored deleted form. Before its songs go,
  the mood is archived to `Users/<name>/archive/moods/<uid>.<YYMMDD-HHMM>.json`.
- **Promotion** happens only in the MusicDatabase menu. It is refused when `tag.db` has a marker
  with that name; nothing changes then.

## The owner's side

Main menu → Users → **Moods**: "Global moods" (create, rename, delete, show songs, add a song,
remove songs), "Users' moods" (any user's moods and songs, without their password; "Promote to a
global mood"), and "Changes from the phones" (who added or removed which song in which mood, and
the result: `added`, `removed`, `unchanged`, `mood_gone`, `song_gone`). Main menu → Users →
**Permissions** ticks permissions per user. `edit_global_moods` is the only one so far, and later
ones will be added to the same list.

## State on the real server

Applied 2026-10-13 14:48, with a backup taken before: `tag.db` migration `0003_tag_kind.sql`
(both existing tags are markers; there are no global moods yet) and `owner`'s `user.db`
migration `0005_moods.sql` (no moods, counter 0). The service was reinstalled with
`--mood-changes /media/owner/T7/Shared/Music/Database/MoodChanges`.

A curl round trip on the LAN address passed with a temporary token:

- `GET /users/owner`, and a 403 with the permission JSON before the permission was granted;
- a user mood: 401 without a token, PUT (song 5332 resolved to 5147), a stale PUT → 409, the
  same name under a new uid merged into the existing uid, DELETE with its archive file, GET
  `since`;
- with the permission granted: on a temporary global mood, add, add of a merged id and remove
  (all 202, applied to `tag.db` with the right results); 404 for the marker's id and for an
  unknown song; 403 again right after the permission was revoked.

The temporary global mood, the change log, the test mood with its archive file, the permission
and the token were removed afterwards. A new user's `user.db` gets the migration at creation;
other users get it at the next MusicDatabase startup with T7 mounted.

Code: `src/utils/database/mood_sync.py` (user moods, shared with the server), `moods.py` (global
moods, promotion, applying the log), `global_mood_log.py` (the log, shared with the server),
`sharing_server.py`, `account_auth.py` (permissions), `migrations/tag/0003_tag_kind.sql`,
`migrations/user/0005_moods.sql`, menu `src/menu/main_menu/users/moods.py`. Runbook:
`docs/runbooks/database.md` → "Moods on the phone". Tests: `tests/test_mood_sync.py`,
`tests/test_moods.py`, `tests/test_database_sharing.py` (permissions and moods section).
