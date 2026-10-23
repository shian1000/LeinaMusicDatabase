# Handoff for the MusicDatabaseApp agent: a fresh MusicDatabase setup for someone else

Written by the MusicDatabase agent (2026-10-21) in answer to the app's
`docs/handoffs/musicdatabase-fresh-setup.md`. All four points are built. Points 1 and 2 have tests,
and the full suite passes (1178 tests). What I decided where your handoff left it open is marked
**(decided)**. Nothing in the phone's contract changed.

## 1. A fresh clone without databases

- `main.py` asks right after taking the app lock, before the backup and the migrations
  (`utils/ui/first_run_prompt.py`; logic in `utils/database/first_run.py`):
  **Download the public catalog** / **Start with an empty database** / **Quit (ask again next
  time)**. It's asked again until one of the first two works. Once the databases exist it's never
  asked.
- **Download:** from `https://github.com/<GITHUB_DB_REPO>/releases/download/database/`, with
  `shian1000/LeinaMusicDatabase` when `GITHUB_DB_REPO` is empty. `.env.example` now leaves
  `GITHUB_DB_REPO` empty instead of `owner/repo`, so copying it unchanged doesn't point at a
  repository that doesn't exist. `version.json` is read first. `format` must be 1, and both files
  must be listed with a size between 1 B and 512 MiB. Each file is downloaded to
  `<name>.download`, must match `size` and `sha256` exactly and pass SQLite's integrity check.
  Only then are both moved in, together. Any failure leaves nothing behind and says why.
  `content_sha256` is ignored.
- **(decided)** If a download runs while the owner is publishing, the new file can meet the old
  `version.json`. That shows up as a SHA-256 mismatch and the owner just tries again. Same
  behaviour you get on the phone.
- **Empty start:** `create_music_db()` / `create_tag_db()` (the models), then the normal startup
  migration run adopts every migration file. I found one gap there and fixed it: tag.db's
  `applied_song_changes` table (tag migration 0002) wasn't in the models, so an empty tag.db had
  no cursor table and every start logged a failed song-reference update. It's a model now
  (`AppliedSongChange` in `tag_db_manager.py`). Checked against the owner's
  schema: same tables, columns, indexes and triggers. The only differences are declared type
  names with the same affinity (VARCHAR vs TEXT).
- **Only tag.db missing:** an empty one is created without asking **(decided)**, because the
  owner's tags wouldn't fit someone else's song ids.
- **How a downloaded catalog differs from the owner's own database:** tag.db holds only global
  moods (today there are none, so it's empty). There are no `spellchecked` / `album_checked`
  markers, so to Spell check / album checks every song is unchecked. `album_covers.path` is empty
  and there's no `covers/` folder (the cover import recreates them). There's no `files.db` (the
  library scan builds it) and there are no users. `schema_version` and `song_id_changes` come
  with it, so the startup migrations apply only what's newer than the published copy (verified:
  0 pending today). The app runs normally on it: verified by a real first run (below) and by a
  test built from `build_public_database()`'s own output.
- Tests: `tests/test_first_run.py`, 21 tests, against a fake release on 127.0.0.1. They never
  reach GitHub.

## 2. Sharing without an external drive

- One rule, `settings.sharing_dir_available(mount, folder)`, used by sharing, users and the
  inbox (mood changes go through `users_available()`):
  - `SHARING_DRIVE_MOUNT` set: exactly as before. The folder is usable only while that mount
    point is mounted, and nothing is ever created under an empty mount point.
  - `SHARING_DRIVE_MOUNT` empty: `SHARING_DIR` must exist and be writable. **(decided)** The app
    never creates it by itself, so a typo can't become a folder. Settings → Server → Folders and
    network offers to create it when you set it.
  - `drive_unavailable_message(mount, folder)` now names the actual case: not set / folder
    missing or read-only / drive not mounted.
- `Settings.sharing_dir` no longer depends on the mount key. Before, an empty mount meant "no
  sharing at all".
- The server: `--mount` is optional. Without it, `data_available()` = `--dir` exists. **For the
  phone nothing changes:** the same 503 everywhere a missing drive gave one (now also "the
  folder is gone"), and the same endpoints, port and names. The systemd unit gets `--mount` only
  when a drive is set, so the owner's unit is byte-for-byte the same.
- Also fixed: with no `SHARING_DIR` at all, the app crashed at startup reading the automatic
  pass's report (`inbox_requests._load_json()` with `INBOX_DIR` None). A newcomer would have hit
  that on the very first run. The daily public-database publish no longer prints "not set up"
  on every start when there's no GitHub token.
- "Setup this PC for database sharing" now warns when the sharing folder isn't usable yet or
  when neither Tailscale nor a LAN interface is available, and offers the first copy right away,
  so a new server has something to serve immediately.
- `.env.example`, the Folders and network labels ("External drive mount point (empty = none)"),
  the runbook and AGENTS.md are updated. Tests are in `tests/test_database_sharing.py` (system
  disk: shared, never created, read-only, the rule's cases, the messages, users + inbox, the
  unit, and the server with and without its command line `--mount`) and
  `tests/test_inbox_requests.py` (no `SHARING_DIR`).

## 3. `MusicDatabase.sh`

Without a TTY it re-launches itself in the first of `x-terminal-emulator`, `gnome-terminal`,
`konsole`, `xfce4-terminal`, `xterm` that exists, using each one's own "run this" syntax and the
script's absolute path. With none of them it says so (stderr plus `notify-send` if it's there).
Without `venv/` it points to the README. Checked with stub terminals on a bare `PATH`.

## 4. README

Rewritten for a newcomer. It covers what the project is and how it relates to Leina Radio, the
requirements, installing (`cd LeinaMusicDatabase`, both ways to make the venv), the first run,
serving phones (folders, setup, users, what to type in *Ustawienia › Serwer*, LAN/Tailscale, no
port forwarding, firewall, linger), optional keys in a table, updating, tests and the licence.
Requirements as written there:

- Linux (Debian 12/13, Ubuntu, Mint); `git`.
- Python ≥ 3.13. Debian 13 / Ubuntu 25.04+: system `python3` + `python3-venv`. Debian 12,
  Ubuntu 24.04, **Linux Mint 22 (Python 3.12)**: `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`,
  then `uv python install 3.13` and `uv venv --python 3.13 --seed venv`). pyenv also works.
- Nothing to compile: every pinned package has a wheel or is pure Python (langdetect and
  wikipedia are built from their pure-Python sources by pip).
- Optional: Google Chrome or a non-snap Chromium (the scraping lookups of Fill missing data),
  `xclip` / `wl-clipboard`, Tailscale.
- Written so both Debian releases work without knowing which one: Debian 13 with its system
  Python 3.13, Debian 12 with 3.13 installed alongside (uv or pyenv); every later step is the same.
- An always-on / headless server: the README explains `sudo loginctl enable-linger <user>` (a
  systemd user service runs only while its user is logged in; linger starts it at boot and keeps
  it after logout) and how to check / undo it. The owner's machine's linger setting was not touched.
- The sharing server runs on the system `/usr/bin/python3` (standard library only). Debian 12's
  3.11 is enough: verified by running the server under 3.11.

## How it was verified (2026-10-21, on the owner's machine)

- A copy of this branch's files (tracked + new, no `.git`, no `.env`, no databases) in an empty
  temp folder, with `HOME` pointed at an empty temp folder and a bare `PATH`. It got its own
  `python3.13 -m venv` and `pip install -r requirements.txt`, with no build errors.
- First run, driven in a pseudo-terminal: "Download the public catalog" fetched the **real**
  release from GitHub (0.9 MiB, published 2026-10-21T11:35:11Z), checked it and reached the main
  menu. 6078 songs, 3714 artists, all 16 + 3 migrations recorded, a backup in `archive/`. A
  second copy chose "Start with an empty database": the real schema, all migrations adopted, no
  errors in `debug.log`.
- In the app: Settings → Server → Folders and network → Sharing folder set to a folder under
  the temp `HOME` (it was offered for creation, mount left empty), restart, Submit database for
  sharing (music.db + tag.db copied), Users → Create a new user.
- The server was started with the exact command `_service_unit()` writes (no `--mount`), on port
  **8012** with the system `python3` (3.14) and with Python 3.11. It answered: `music.db` 200
  and byte-identical, `POST /login` 200 with a token, the user's playlists / listens 200, 503
  while the folder was renamed away and 200 again after. It was stopped by its own PID. The
  owner's `musicdatabase-share` on 8002 was never touched.
- The full suite passed in that copy's own venv (1178 tests) and in the worktree.

**Not simulated here:** a real Debian / Mint system (I used this Ubuntu-based machine's
python3.13, plus uv's 3.11 for the server), `systemctl --user` installing the service (it would
have collided with the owner's live unit of the same name), a real phone, a LAN-only setup
without Tailscale, and `MusicDatabase.sh` with a real double-click. The owner's test on Linux
Mint covers these.

## For the phone side

Nothing to change. A friend's server speaks the same protocol on the same port. Its catalog
starts as the owner's public copy, so song ids match the owner's at first and drift apart as each
side edits its own. A phone switching between the two servers is a full re-download, as with any
other server.
