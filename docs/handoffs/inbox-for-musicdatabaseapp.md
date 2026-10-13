# Handoff for the MusicDatabaseApp agent: the inbox is live

Written by the MusicDatabase agent (2026-10-13) in answer to the app's
`docs/handoffs/musicdatabase-outbox.md`. The phone can now send its outbox.
Updated the same day for login: signed request names, 401/403 (the app's
`docs/decisions/0006-musicdatabase-login.md`); the phone sends signed requests
and has been tested against the real server.

## Sending

`PUT http://<server>:8002/inbox/<file name>`, body = the request file's bytes,
with `Content-Length` (no chunked encoding). Same server, port and addresses as
the database download (LAN `192.0.2.13`, Tailscale `100.64.0.10`).

- File name: `[<user>_]YYYY-MM-DD_HH-MM-SS_NNN.db` — the user part is a
  MusicDatabase username (`USERNAME_PATTERN` in `account_auth.py`: 2–32 of
  `a-z 0-9 _ -`, lowercase, starting with a letter or digit), number 3–9
  digits. Anything else is refused.
- A name with a user part must be sent with that user's token from
  `POST /login` (`Authorization: Bearer <token>`); the user part is always a
  verified sender. A name without one is accepted without a token, or with a
  valid one of any user (a revoked one still gets 401).
- Body must start with the SQLite header (`SQLite format 3\0`).
- At most 1 MiB per file.

## Answers

| Code | Meaning | What the phone should do |
|---|---|---|
| 201 | stored | drop it from the outbox |
| 200 | this exact file was already received (waiting or processed) | drop it from the outbox |
| 409 | another file with this name was received | bug on the phone (number reused) — keep it, don't retry blindly |
| 400 | bad name or not an SQLite file | bug on the phone — keep it |
| 401 | a token is needed (the name has a user part) or was sent, but it's missing, malformed or revoked/unknown (re-password, rename, delete, logout) | log in again; keep it |
| 403 | the token belongs to another user than the name's user part | keep it for its own user, send the rest |
| 411 | no `Content-Length` | send with a length |
| 413 | over 1 MiB | split the request |
| 507 | inbox full (unprocessed requests reached 100 MiB) | keep it, retry later (e.g. on the next daily download) |
| 503 | the server's drive isn't mounted | keep it, retry later |
| no answer | server/network down | keep it, retry later |

A resend after a lost answer is safe: it gets 200. Inbox files are never served
back (`GET /inbox/...` is 404).

## What happens on the MusicDatabase side

Requests are reviewed by the owner (Manage database → "Resolve new requests"), never
applied automatically. Reports whose song was deleted or whose link already
changed are closed without asking. For the rest MusicDatabase checks with yt-dlp
whether the dead link and the phone's replacement play, runs its own matcher,
and the owner picks: the phone's video, its own, a pasted link, "N/A", keep the
current link, or later. The result reaches the phone the usual way, in the next
downloaded `music.db`. Format 1 and `dead_youtube_links` are understood; a file
with another `format` or an unknown table is kept unprocessed for a later version.
