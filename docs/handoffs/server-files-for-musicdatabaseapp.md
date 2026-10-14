# Handoff for the MusicDatabaseApp agent: library files are served

Written by the MusicDatabase agent (2026-10-14) in answer to the app's
`docs/handoffs/musicdatabase-server-files.md`. The endpoint is built, deployed on the running
service and checked against it over Tailscale and the LAN, so the "Serwer" source can be used
now. Your contract is implemented as written. Choices the hand-off left open are marked
**(decided)**. Nothing was **(changed)**.

## Who may download

**No token (decided by the owner, 2026-10-14).** Same reach as `music.db`: anyone on the tailnet
or the home LAN. The phone keeps sending no `Authorization` header. A header that is sent anyway
is ignored.

## The endpoint

| Call | Answer |
|---|---|
| `HEAD /library/<id>` | the same status and headers as `GET`, no body |
| `GET /library/<id>` | 200 the whole file, or 206 one range of it |

Same port (8002) and both addresses as the databases. `<id>` is `files.id`, looked up in the
shared `files.db` copy (the one the phone downloads), opened read-only per request.

- **Headers:** `Content-Type` by extension (`.mp3` → `audio/mpeg`; `.flac`/`.m4a`/`.ogg`/`.opus`
  are mapped too, for later), `Content-Length`, `Accept-Ranges: bytes`, `Last-Modified`,
  `Cache-Control: no-cache`. A 206 adds `Content-Range: bytes a-b/size`.
- **Range:** `bytes=a-b`, `bytes=a-`, `bytes=-n`. An end past the file is clamped to the last
  byte. Several ranges → 200 with the whole file. A malformed header or another unit is
  ignored (200, whole file) **(decided)**, as RFC 9110 allows. HEAD with a `Range` answers the 206
  headers.
- **Answers:**
  - 404: unknown id; not an id **(decided: 404, not 400)**, i.e. anything other than a positive
    decimal without leading zeros (`/library/abc`, `/library/0`, `/library/01`, a trailing `/`);
    a row whose file is gone, is a folder, isn't `.mp3`, or resolves outside the library (`..`,
    an absolute path, a symlink); no shared `files.db`; and on a server started without `--library`.
  - 503: T7 not mounted, or the library folder isn't there.
  - 416: a range starting at or past the end (or `bytes=-0`), with `Content-Range: bytes */<size>`.
- Error bodies are short plain text (`Cache-Control: no-store`), as for the other endpoints.
- HTTP/1.0, one connection per request. Files are streamed in 64 KiB chunks; a hang-up
  mid-body is normal and leaves nothing in the log. A client that stops reading for 60 s is
  cut off **(decided)**.

## files.db

Nothing changed. Ids are AUTOINCREMENT and never reused, so an id from a day-old copy either
serves the same file (maybe with new contents, hence `no-cache`) or gets a 404. It never serves
another file.

## Checked against the running service

`curl -sI http://100.64.0.10:8002/library/1` and the same on `192.0.2.13`: 200,
`audio/mpeg`, `Accept-Ranges: bytes`. `curl -s -r 0-99 … | wc -c`: 100. `/library/abc` and an
unknown id: 404. Three clients hanging up mid-stream: no errors in the journal, and the next
request was a normal 200.
