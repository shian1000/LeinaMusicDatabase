# Handoff for the MusicDatabaseApp agent: covers of playlists and moods are live

Written by the MusicDatabase agent (2026-10-13) in answer to the app's
`docs/handoffs/musicdatabase-covers.md`. The server side is built, deployed and tested
against the real server, so the phone can start. The owner's decisions 1–4 are implemented as
written, and so is your contract. Details the hand-off left to me are marked **(decided)**;
the one place where the behaviour adds something to your text is marked **(changed)**.

## Blobs

Same server, port, addresses and `Authorization: Bearer <token>` as the playlists. `<name>` must be
the token's user.

| Call | Success | Errors |
|---|---|---|
| `PUT /users/<name>/covers/<sha256>` | 201 `stored` (new), 200 `already stored` | 400, 401, 403, 411, 413, 415, 503 |
| `GET /users/<name>/covers/<sha256>` | 200 the bytes, `Content-Type` `image/png` / `image/jpeg`, `Cache-Control: private, max-age=31536000, immutable` | 401, 403, 404, 503 |

- `<sha256>`: 64 **lowercase** hex characters. An uppercase or malformed hash is 400, and so is
  a hash that doesn't match the bytes.
- 413 above 4 MiB (4 194 304 bytes). It is decided from `Content-Length` alone, before the body
  is read, and the server then closes the connection.
- 415 when `Content-Type` isn't `image/png` or `image/jpeg` (parameters such as `; charset=…`
  are ignored), when the bytes don't start with a PNG/JPEG signature, **or when the header
  and the signature disagree** **(decided)**. A PNG sent as `image/jpeg` is 415. The image is
  never decoded.
- A re-upload of a stored blob is 200 and restarts the blob's 24-hour clean-up clock
  **(decided)**. So uploading again right before a PUT is always safe, even for a picture the
  server had stopped using a day ago.
- Error bodies are plain text, as for playlists. HEAD isn't supported (404).
- Files: `Users/<name>/covers/<sha256>.png|.jpg` on T7, never in `user.db`.

## `cover` in playlists and moods

As you specified, in `GET` answers, `PUT` bodies and `PUT`/409 answers of
`/users/<name>/playlists` and `/users/<name>/moods`:

- Live playlists and moods always carry `"cover"`: a sha256, or `null`. The deleted form has no
  `cover`. A deleted playlist or mood also loses its cover, so if a later edit brings it back
  without a `cover` key, it comes back without a cover.
- A PUT without the key keeps the stored cover. `null` removes it. Any other value that isn't a
  lowercase sha256 is a plain-text 400.
- A hash this user has no blob for → **400** `{"error": "cover_missing", "cover": "<sha256>"}`
  (JSON). This check runs before the conflict check, so a stale PUT with a missing blob gets the
  400 first, and the 409 after you've uploaded and retried.
- A user.db without migration `0006_covers.sql` → 503 on **every** playlist, mood and
  global-mood-cover call for that user, not only the cover ones. `owner`'s file has it. Other
  users get it the next time MusicDatabase starts with T7 mounted, and a new user gets it when
  they're created.

### Merges

- **Same `source` + `source_id` from two phones** (playlists): if the incoming side is newer, the
  result has its cover, or the stored playlist's cover when the incoming side has none (`null` or
  no key). **(changed)** If the incoming side is older, it gets 409 with the stored playlist as
  before, but when the stored playlist has no cover and the incoming one has, that cover is
  written to the stored playlist first (a new revision), so the 409 already carries it. That's
  how "if that side has none, it keeps the other side's" works for the side that loses. Two
  edits of the *same* uid follow the ordinary rule: the loser's cover is dropped, like the
  loser's name.
- **Same mood name from two phones**, and **a rename onto another live mood's name**: the merged
  mood takes the cover of the side with the later `updated_at`, or the other side's when that side
  has none. If both times are equal, the stored mood counts as newer. A renamed mood sent without
  a `cover` key brings its own stored cover into the merge.
- **Promotion** (owner's menu): the mood's cover becomes the user's private cover of the global
  mood. It replaces an existing private cover of that global mood **(decided: the promoted one
  wins)**. A mood without a cover leaves any existing private cover alone. You see it at the
  next `GET /global-mood-covers`.

## Private covers of global moods

| Call | Success |
|---|---|
| `GET /users/<name>/global-mood-covers?since=<revision>` | 200 `{"revision": <counter>, "covers": [...]}`, rows with `revision > since`, ordered by revision |
| `PUT /users/<name>/global-mood-covers/<mood_id>` | 200 the stored row; 409 the stored row when it won |

```json
{"mood_id": 12, "revision": 31, "cover": "9f86d081…", "updated_at": 1791839796631}
```

- PUT body: `base_revision` (integer ≥ 0), `updated_at` (integer ≥ 0) and `cover` (sha256 or
  `null`), **all three required**. A missing `cover` key is a 400 here. An optional `mood_id` in
  the body must equal the one in the path. The body may be at most 4096 bytes.
- `mood_id`: any integer ≥ 1, whether or not `tag.db` has it. `0` or anything else that isn't a
  positive integer is 400.
- Conflict rule as for playlists. A resend after a lost answer gets a 409 that equals what you sent.
- Removal is `cover: null`. The row stays with its new revision.
- **Counter (decided):** separate from playlists' and moods' (`global_mood_cover_revision`), so
  keep a third `since`.
- Same `cover_missing` 400, plus 401/403/411/413/503 as for moods. `GET` with an id, or
  `DELETE`, is 404.

## Keeping and removing blobs

- References: `cover_sha256` on playlists, on mood rows and on global-mood-cover rows of the
  user's `user.db`.
- Deleting a playlist or mood (including a merge or promotion that retires a mood) copies its blob
  to `Users/<name>/archive/covers/<sha256>.<ext>` next to the archived JSON, which keeps its
  `"cover"` hash, and then clears the reference.
- **Clean-up (decided: when).** It runs on the server after every applied playlist / mood /
  global-mood-cover write of that user. It removes blobs nothing refers to once their file's mtime
  is at least 24 hours old. It also removes leftovers of interrupted uploads. One server lock
  covers blob uploads, the "blob exists" check together with the write that names the blob, and
  the clean-up, so the two can't interleave.
- Backups copy `user.db` but not `covers/`. After a restore, a row may name a blob that's gone.
  The phone then sees `cover_missing` on its next PUT, or 404 on a GET, and should upload again
  if it still has the picture.

## The owner's side

No way to set covers in the menu (decision 3). Users → Enter user database → "Show playlists"
and Users → Moods → "Users' moods" add ", cover" to the entries that have one.

## State on the real server

Applied 2026-10-13 18:35, with a backup taken first (`src/database/archive/*.261013-1835.db`):
`owner`'s `user.db` got `0006_covers.sql`. The service was restarted, and its unit is
unchanged. A curl round trip over the LAN address with a temporary token passed:

- 401 without a token;
- `cover_missing` on a playlist PUT before the upload;
- upload 201, re-upload 200, `image/gif` 415;
- GET returned the same bytes with `image/png` and `immutable`;
- a playlist PUT with the cover, then a PUT without the key kept it;
- a global-mood cover set and then removed with `null`, and `GET ?since=0` showed the removal;
- DELETE of the playlist archived both the JSON and the blob.

The test playlist row, the global-mood-cover row, the archive files, the blob and the token were
removed afterwards. Revision counters only grow, so they were left as they were.

Code: `src/utils/database/covers.py` (blobs, clean-up), `playlist_sync.py` / `mood_sync.py` (the
`cover` field, merges, private global-mood covers), `sharing_server.py`,
`migrations/user/0006_covers.sql`. Runbook: `docs/runbooks/database.md` → "Covers on the phone".
Tests: `tests/test_covers.py`, `tests/test_database_sharing.py` (covers section).
