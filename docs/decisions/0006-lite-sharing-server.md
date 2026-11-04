# ADR-0006: MusicDatabase Lite - a standalone sharing server, beside the full program

- Status: Proposed (2026-11-04). A plan for later, not built. The owner will carry it out as a new
  program.
- Date: 2026-11-04

## Context

MusicDatabase is two things in one repo:

1. **The full program** (`main.py`). A terminal app that builds and enriches the catalog
   (MusicBrainz, Wikipedia, iTunes, Genius, YouTube, Spotify scraping with Selenium), imports mp3
   tags and YouTube playlists, indexes the music library, reviews Leina Player's requests and
   manages users. It has many dependencies (`requirements.txt`).
2. **The sharing server** (`src/utils/database/sharing_server.py`). A standard-library-only
   script, run as a systemd user unit, that serves Leina Player:
   - the catalog copies (`music.db`, `tag.db`, `files.db`);
   - the library's audio files (`/library/<id>`);
   - logins and tokens;
   - sync of a user's playlists, moods, folders, saved songs, links, listens and covers;
   - the inbox for requests;
   - global mood changes.

   It has served HTTPS with its own pinned certificate since 2026-11-04.

The server is already "lite" in what it imports, but it can't stand alone. It depends on the full
program for:

- the shared copies of the catalog (made by `sharing.share_databases()`);
- `files.db` (the library index, matched to songs by the import engine's `LocalSongMatcher`);
- users and passwords (`users.py`, main menu -> Users);
- user.db migrations (`migrations/user/`; the server never migrates);
- settling requests (`inbox_*.py`) and applying global mood changes (`moods.py`).

The owner would like a second way to run a Leina Player server: **MusicDatabase Lite**. It would
only share music and sync, with the smallest possible set of imported libraries and no scraping,
for people who want their own server without the enrichment machinery (friends, a small home
server). A user picks one or the other: Lite or the full program. Both serve the same Leina Player.

## Decision (proposed)

### Three layers instead of two copies

| Layer | Holds | Imports |
|---|---|---|
| **Core** (a package of its own, e.g. `musicdatabase_core`) | database schemas and migrations (music.db read side, user.db full), account and token rules (`account_auth`), sync rules (`playlist_sync`, `mood_sync`, `folder_sync`, `saved_sync`, `listen_sync`, `covers`, `global_mood_log`), the request file format, `certificate_code()` | standard library only (as these modules are today) |
| **Full program** (this repo) | everything it has now, using the core instead of its own copies | as today |
| **Lite** (a new program) | the HTTP server, a simple library index, user administration, setup | core + a short approved list (below) |

The sync and auth rules exist exactly once, in the core. A fix there reaches both programs. Two
copies of the same rules would drift apart within months; that is the main reason for the core.

### What Lite does

- **Serves the same API as today's sharing server**, path for path: the same status codes,
  headers, JSON shapes, byte ranges for `/library/<id>`, `Cache-Control` and the Host check.
  Leina Player must work with either server unchanged. A contract test suite, run against both
  servers, is the proof (phase 2).
- **The catalog** is downloaded from the public GitHub release (`version.json` + `music.db` +
  `tag.db`, checked by size and SHA-256, swapped in as a pair). These are the same files and
  checks as the full program's first start (`first_run.py`). No building or enriching.
- **The library index** (`files.db`): a scan of the music folder, read with a simple tag reader,
  matched to the catalog by a deliberately simplified rule (normalized artist + title, as
  ADR-0003's "lite logic"). Files it can't match stay unmatched, never guessed. The full
  program's `LocalSongMatcher` is not ported.
- **Users**: create, re-password, rename and delete, from a small CLI (later perhaps a web page),
  with the same `account.json` / `tokens.json` format (the core).
- **Requests**: stored exactly as today (inbox with the same limits, anonymous uploads included),
  never settled. Lite offers them as a package (`request_packages.py` format) for the full
  program's owner, or simply keeps them.
- **Global moods**: changes recorded (as today); applying them needs the full program, so on Lite
  without one they stay pending.
- **HTTPS**: its own certificate, pinned by Leina Player through the same code
  (`certificate_code()` in the core), on by default.

### Technology

- **FastAPI + Pydantic + uvicorn** are allowed in Lite: it's a new program with a virtual
  environment (or a container) from day one, so they cost no rewrite. Pydantic models describe
  every request and response body, and the contract tests check them against the core's rules.
  The one hard requirement is the existing API contract, byte for byte where Leina Player
  depends on it.
- Deployment: a systemd user unit (as today) or a container image. Settings in `.env`, as in the
  full program.

### Owner's rule: control over every import

The owner wants control over every library Lite imports (2026-11-04):

- **Every third-party package is approved by the owner before it's added.** This covers the
  direct ones and, through the lock file, every transitive one. Each comes with a written reason
  in a `DEPENDENCIES.md` (what it's for, licence, why the standard library won't do) and is
  pinned to an exact version. A new transitive dependency after an upgrade is an approval too.
- **The standard library first.** A package is added only when the standard library has no
  reasonable way to do it.
- **Enforced by a test, not by memory.** A test walks Lite's source and fails on any import
  outside an allow-list: the standard library, the core, and the approved packages. Adding a
  package means editing that list, which makes it visible in review.
- **The core stays standard-library only**, enforced the same way, so the full program and Lite
  never pull a dependency in through it.
- **Licences**: GPL-compatible only (MIT, BSD, Apache 2.0, LGPL, GPLv2-or-later, GPLv3), as for
  the full program.
- **Upgrades** are deliberate: no automatic updates. An upgrade lists what changed in the lock
  file for the owner to approve.

A starting list to approve (proposal, not decided):

| Package | Why |
|---|---|
| `fastapi` | routing, request/response models, OpenAPI docs |
| `pydantic` | the body models (comes with FastAPI) |
| `uvicorn` | the ASGI server, TLS with the server's own certificate |
| a tag reader (`mutagen`, as the full program uses) | reading artist/title for the library index |

FastAPI pulls in `starlette`, `pydantic-core`, `annotated-types`, `typing-extensions`, `anyio`,
`idna`, `sniffio`, `h11` and `click` (roughly, depending on versions). All of them go through the
same approval.

### Phases

1. **Extract the core** in this repo. Move the standard-library modules above into a package
   both the full program and today's server import. There is no behaviour change; the existing
   tests (~2,260) must pass unchanged.
2. **A contract test suite** for the sharing API: every endpoint, status, header and JSON shape
   Leina Player relies on (the app's ADRs 0002-0015 and `sharing_server.py`'s docstring). Run it
   against today's server first; it becomes the specification.
3. **Lite's server** on FastAPI, using the core, passing the contract suite. The import
   allow-list test exists from its first commit.
4. **Lite's data side**: catalog download from GitHub, the simple library index, the user CLI,
   setup (a wizard like `sharing_wizard.py`, asking before every change).
5. **Run it next to the full program** (another port) with a test phone, then hand it to a first
   outside user.

## Consequences

- One more program to release and keep running; the core keeps the shared logic in one place,
  but Lite's own parts (server, index, CLI) are new code to maintain.
- Lite's library index matches fewer files than the full program's (no fuzzy matching, no
  review), so some songs won't stream from a Lite server. That's acceptable for "lite"; a user
  who wants more runs the full program.
- Leina Player needs no change as long as the contract suite passes. A change to the API from
  then on means changing the contract suite, the core and both servers together.
- The full program keeps today's standard-library server until there's a reason to move it onto
  Lite's server; the two may converge later, which is a separate decision.

## Open questions (for the owner, before phase 1)

- Lite as a folder in this repo or a repo of its own? It would share the core either way, as a
  package installed from this repo or published.
- A web page for user administration in Lite, or the CLI only?
- Container image, systemd unit, or both?
- Should Lite accept requests at all when nobody runs the full program, or switch the inbox off
  by default?
