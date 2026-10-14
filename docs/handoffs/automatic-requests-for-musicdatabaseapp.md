# Handoff for the MusicDatabaseApp agent: requests are now settled automatically where safe

Written by the MusicDatabase agent (2026-10-14). Nothing in the request format,
the endpoints or the answer codes changed — Leina Radio keeps working as it
does. This is what changed on the MusicDatabase side and the one new value the
app will see: a user permission.

## What happens to a request now

Until now every request waited until the owner reviewed it in MusicDatabase.
Now an automatic pass runs every 15 minutes on the server (while the
MusicDatabase program isn't open — then it waits until it is, where the owner
runs the same pass before reviewing). It settles on its own:

- **Dead YouTube links:** the song's link is replaced when Leina Radio's
  suggested video (`replacement_video_id`) and MusicDatabase's own search found
  **the same video** and it plays in Poland. A suggestion our search doesn't
  agree with, or no suggestion, waits for the owner as before. So a good
  `replacement_video_id` now matters more: it's what can get a link fixed
  without waiting.
- **Field corrections:** saved without review only when the request was sent
  by a logged-in **user who has the `apply_corrections` permission** (see
  below) and nothing conflicts: the value can be stored, music.db still holds
  the `old_value` Leina Radio had, no other request asked for another value,
  the song still has that title / artist / album, and saving would merge
  nothing (an artist renamed to another artist's name, an album retitled to
  one the artist already has) nor leave the choice of renaming a whole album.
  Anything else waits for the owner.
- What needs no answer is settled as before (song gone, value already there, ...).

After any automatic change MusicDatabase copies the databases for sharing
again, so the fixed link / value arrives with Leina Radio's next download of
`music.db` — no need to wait for the daily copy.

## The new permission: `apply_corrections`

It is a permission of a **MusicDatabase user**, not of a device: the owner
grants it per user (MusicDatabase main menu → Users → Permissions), stored in
that user's `account.json` like `edit_global_moods`. It applies to every request
that user signs, from whichever device.

- It appears in the existing `permissions` lists: `POST /login` → 200
  `{"username", "token", "permissions": [...]}` and `GET /users/<name>` → 200
  `{"username", "permissions": [...]}`. Possible values now:
  `edit_global_moods`, `apply_corrections`.
- **Please check the app tolerates a permission name it doesn't know** (ignores
  it rather than failing to parse the login answer). That's the one thing that
  must work; future permissions will be added the same way.
- The server itself doesn't check it — no endpoint answers 403 for it. Only a
  **signed** request counts: a file named `<user>_YYYY-MM-DD_HH-MM-SS_NNN.db`
  sent with that user's token (the inbox handoff). An unsigned request
  (no user part) is always reviewed by the owner, whoever sent it.
- Optional, if you want to show it: for a user with `apply_corrections`, a
  sent correction can be described as "saved within ~15 minutes unless
  something conflicts"; without it, "waits for review". Don't promise it —
  any conflict above still sends it to the owner.

## Inbox answers (unchanged codes, slightly different timing)

Files the automatic pass can't settle move from `Inbox/new/` to
`Inbox/review/` on the server. For Leina Radio:

- a resend of a file waiting there is still **200** "already received";
- files waiting for review still count towards the 100 MiB inbox limit, so a
  **507** lasts until the owner has reviewed some — keep retrying later, as now.

## What to tell the owner back

Whether Leina Radio ignored the unknown permission correctly (or what had to
change), and whether you want the app to show anything for `apply_corrections`.
