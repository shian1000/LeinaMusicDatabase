# Handoff for the MusicDatabaseApp agent: the public database on GitHub and request packages

Written by the MusicDatabase agent (2026-10-21) in answer to the app's
`docs/handoffs/musicdatabase-public-database.md` (app ADR 0014). Both parts are built and covered
by tests: the public export with its self-check, publishing to a GitHub Release (daily and from
the menu), and importing request packages. **Status of the first real publish:** see the last
section. Your contract is implemented as written; what I changed is marked **(changed)**, the
details it left to me **(decided)**.

## 1. Downloading the public database

- **URLs:** `https://github.com/<owner>/<repo>/releases/download/database/<file>`, files
  `music.db`, `tag.db`, `version.json`. For the owner's catalog `<owner>/<repo>` is this
  repository, LeinaMusicDatabase (the default your handoff already names). The release is not marked "latest", so other releases of the repo
  never move it. GitHub answers these URLs with a **302 redirect** to its file host — follow
  redirects. No token needed (public repo).
- **Uncompressed (decided):** plain SQLite files (today music.db ≈ 0.95 MB, tag.db ≈ 36 KB).
- **`version.json`**, uploaded **last** on every publish:

  ```json
  {"format": 1,
   "published_at": "2026-10-21T12:00:00Z",
   "files": {"music.db": {"size": 966656, "sha256": "…", "content_sha256": "…"},
             "tag.db":   {"size": 36864,  "sha256": "…", "content_sha256": "…"}}}
  ```

  `published_at` is UTC. `sha256` is the file's own SHA-256 (hex, lowercase) — check it and `size`
  before swapping your copy. **(changed)** `content_sha256` is extra: a hash of the schema and rows
  that MusicDatabase uses to decide "changed since the last publish"; ignore it, and ignore any
  other key you don't know. A `format` other than 1 means a layout you don't know — keep your copy.
- **When it changes:** once a day when the owner opens MusicDatabase (after its daily backup),
  only when the catalog's content changed, plus whenever the owner publishes from the menu. Days
  without a publish are normal.
- **During a publish** each database is uploaded under a temporary name, then the old asset is
  deleted and the new one renamed: for a moment a file can be **404**, or already newer than the
  `version.json` you read. Your size + hash check catches both; keep the old copy and try again
  later (next day is fine). Read `version.json` first, then download only files whose `sha256`
  changed, and swap only when every downloaded file matches — then the pair is consistent.

## 2. What the public files hold

Same schema as the sharing server's copies — every table and column your `song_catalog.dart`,
`moods.dart` and `song_details.dart` read is there. Built by an **allow-list**: a table not on it
is dropped; a column not on it stays in the schema but is **NULL**.

- `music.db`: `songs`, `artists`, `albums`, `additional_song_artists`, `song_id_changes`,
  `bad_youtube_videos`, `rejected_song_requests`, `album_covers`, `schema_version` (and SQLite's
  `sqlite_sequence`). All values kept **except `album_covers.path`, always NULL** (a file name in
  MusicDatabase's covers folder; the table is empty today anyway).
- `tag.db`: `tags` **only `kind = 'mood'` rows (decided)** — the internal markers
  `spellchecked` / `album_checked` and their `song_tags` rows are left out, they're the owner's
  work state and you only read moods; `song_tags` of those moods; `applied_song_changes`;
  `schema_version`.
- Never in it: `files.db` (so no library files — the "Serwer" source isn't available from
  GitHub), anything from `user.db` (accounts, tokens, permissions, playlists, users' own moods,
  folders, covers, listens, saved songs), the inbox, local paths, lyrics.
- **(changed, also on the server's copies)** `songs.nostalgic`, `songs.melancholic` and
  `songs.party` no longer exist: the owner said they were obsolete, never filled, and they were
  dropped by music.db migration 0016. You never read them; nothing to do unless some code lists
  `songs` columns explicitly.
- Before anything is uploaded, MusicDatabase reopens both files and refuses to publish when a
  table isn't allowed (a user's or `files.db`'s by name), a withheld column has a value, a marker
  row slipped in, a text value looks like a local path (`/home/`, `/media/`, `smb://`, `C:\`, …),
  or SQLite's integrity check fails.

## 3. Request packages

- **Package (decided):** a `.zip` whose entries are the outbox files **at the top level, names
  unchanged** (`[<user>_]YYYY-MM-DD_HH-MM-SS_NNN.db`), contents unchanged. Nothing else: **no
  folders, no manifest, no other file** — any such entry refuses the whole package. Deflate or
  stored, no encryption. At most **1000 files**, each at most **1 MiB** (the inbox's limit), at
  most **50 MiB** together. The file name of the `.zip` itself is free.
- **Sender name (decided):** the inbox's username pattern `^[a-z0-9][a-z0-9_-]{1,31}$` — 2 to 32
  characters, lowercase `a`–`z`, digits, `_` and `-`, the first a letter or digit. Clean what the
  user types: lowercase, strip diacritics / transliterate to Latin, spaces → `-`, drop everything else, cut to 32. If fewer than 2 characters are left, send
  the files **without** a user part (anonymous) rather than inventing one.
- **It is never a login.** The owner's import records the package's files, so a typed name equal
  to a real account's still gets **none** of that account's permissions: corrections from a package
  are always left for the owner's review. The review shows each such file as
  `<file> (from <name>, imported from a package)`.
- **Auto-processing (decided):** the automatic pass treats imported files like any other request:
  a dead link is replaced / a found link saved only when your suggestion and MusicDatabase's own
  search agree and the video plays; song requests are never added automatically; field
  corrections never auto-saved (above).
- **Imported twice:** a file whose name is already in the inbox (waiting, reviewed or processed)
  is skipped and named in the owner's report; nothing is duplicated or overwritten. So a phone
  may safely include files it isn't sure were sent, but don't reuse a name for different content.
- Once exported, the files count as sent on the phone (your decision); there is no answer back.

## First real publish

Done 2026-10-21 (`published_at` 2026-10-21T11:35:11Z), asked by the owner: the release `database`
in LeinaMusicDatabase holds `music.db` (950 272 B, 6078 songs, integrity ok), `tag.db` (36 864 B —
no global moods exist yet, so `tags` / `song_tags` are empty; markers are left out) and
`version.json`. All three downloaded without a token through the public URLs (redirects followed),
sizes and SHA-256 equal to `version.json`. A second publish right after answered "unchanged" and
uploaded nothing. The next publish (at MusicDatabase's next start, after migration 0016) will
change `music.db`'s hash: `songs` loses the three unused mood-flag columns.
