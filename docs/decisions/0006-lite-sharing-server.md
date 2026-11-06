# ADR-0006: MusicDatabaseWired - a public sharing server, working with MusicDatabaseHomeServer

- Status: Accepted as a plan (2026-11-06, owner's decisions). Phase 1 done (`src/musicdatabase_core/`,
  the old module names kept as redirects); MDW itself isn't built. Replaces the first version
  of this ADR (2026-11-04), which called the new program "MusicDatabase Lite" and had users pick one
  program or the other.
- Date: 2026-11-06

## Names

- **MusicDatabaseHomeServer (MDHS)**: this program, so far called MusicDatabase. The new name
  reaches docs and menus first. The repository keeps its name for now, since Leina Player has
  `shian1000/LeinaMusicDatabase` as its default catalog source.
- **MusicDatabaseWired (MDW)**: the new program.

## Context

MDHS is two things in one repo:

1. **The full program** (`main.py`). A terminal app that builds and enriches the catalog
   (MusicBrainz, Wikipedia, iTunes, Genius, YouTube, Spotify scraping with Selenium), imports mp3
   tags and YouTube playlists, indexes the music library, reviews Leina Player's requests and
   manages users. It has many dependencies (`requirements.txt`).
2. **The sharing server** (`src/utils/database/sharing_server.py`). A standard-library-only script,
   run as a systemd user unit, that serves Leina Player: catalog copies, library audio files,
   logins and tokens, sync of a user's data, the request inbox, and global mood changes.

The owner wants friends to sync and send requests without a VPN, and without the home server being
reachable from the internet. The server can't simply be moved to a rented machine, because it
depends on the full program for the catalog copies, `files.db`, users and passwords, user.db
migrations and settling requests.

## Decision

### Three layers

| Layer | Holds | Imports |
|---|---|---|
| **Core** (a package of its own) | see "What goes into the core" | standard library only |
| **MDHS** (this program) | everything it has now, using the core instead of its own copies | as today |
| **MDW** (a new program, `wired/` in this repo) | the HTTP server, request vetting, user administration, setup | core + a short approved list |

The sync and auth rules exist exactly once, in the core. Two copies would drift apart within months.
MDW lives in this repo so that the core and both programs can change in one commit. It can be split
off later.

### What goes into the core

- **Moved** (they import only the standard library and each other): `account_auth`,
  `user_covers` (formerly `covers`), `playlist_sync`, `mood_sync`, `folder_sync`, `saved_sync`, `listen_sync`,
  `global_mood_log`.
- **Cut out of `sharing_server.py`**: `REQUEST_NAME_RE`, `SQLITE_HEADER`, `certificate_code()`.
- **Extracted, because they depend on `Settings` or SQLAlchemy today**: creating an account from
  `users.py`, applying `migrations/user/` from `migrations.py`, and replaying `song_id_changes` into
  a user.db from `song_references.py`, rewritten on plain `sqlite3`.

Everything else stays in MDHS: `inbox_*`, `moods.py`, `sharing.py`, `sharing_server.py`,
`public_export`, `files_index`, the imports and the scraping.

### MDW is a small base plus modules

The base holds only what the server needs in order to exchange data: the catalog, accounts, sync
and the inbox. Modules are ordinary packages switched on in `.env`, each with its own routes and its
own approved libraries (`mutagen` only in a library module, for example). There is no plugin loader.
The library index and audio streaming are a later module, not part of the base. The phone already
accepts a 404 for `files.db`, so a server without it works.

### Who does what

Every piece of data has one writer.

| Data | MDW | MDHS |
|---|---|---|
| Catalog (`music.db`, `tag.db`) | reads and serves a copy | the only writer |
| Scraping (albums, years), imports | never | does it |
| Accounts and data of MDW's users | the only writer | writes only through MDW's API |
| Accounts and data of MDHS's users | doesn't have them | as today |
| Requests | receives and vets them | settles them |
| Global moods | off at first | as today |

Work for MDHS is not a new job system, since the inbox already is a queue:

1. A phone sends a request to MDW, which vets it.
2. MDHS connects to MDW and fetches a package of vetted requests. The home machine connects out and
   accepts nothing from the internet.
3. The package goes through the existing `request_packages.py`, so these files never act with an
   account's permissions. A friend with the same login as a user of MDHS gets nothing.
4. The improved catalog returns through the public GitHub release, which MDW downloads with the
   checks `first_run.py` uses.

### Playlists made in MDHS for MDW's users

They are made in the program, as today (folders, comparing with a YouTube playlist, reviews). MDHS
then logs in to MDW as that user and sends them through the normal API
(`PUT /users/<name>/playlists`), like a phone. The same sync rules apply and no new endpoint is
needed. MDHS may know a song MDW's catalog doesn't have yet, so a playlist goes after the catalog is
published, or sends such a track without a song id.

### Vetting requests in MDW

Today's server checks only the token, name, size and SQLite header when receiving a file, and only
MDHS reads its contents. MDW adds a check before anything is passed on:

1. **Receiving**: the current checks and limits. Requests without a login are off by default.
2. **Structure**: opened read-only with `trusted_schema` off; only known tables and columns, no
   views or triggers, limited row counts.
3. **Contents**: every row through a Pydantic model (types, lengths, the shape of a video id, the
   fields a correction may name).
4. **Sense**: whether the song exists in the catalog (a warning only, as the catalog may lag),
   whether it's a duplicate, whether it was already rejected (`rejected_song_requests`).
5. **Rewriting**: the checked data goes into a new, clean file in the same format, so MDHS never
   opens a file made by a stranger and its reader needs no change.

A file ends up in one of three states:

- **Accepted**: answered 201 and offered to MDHS.
- **Refused**: answered 400 with the reason kept on the server. Leina Player moves a file answered
  400 to its `rejected` folder and doesn't send it again.
- **Held**: a request kind this MDW doesn't know, from a newer app. Answered 201 and kept until MDW
  is updated, never lost.

MDW judges whether a request is well formed and safe. Whether a correction is right stays the
owner's decision in MDHS. MDHS keeps its own checks and treats these files as untrusted, since a
compromised MDW must not give access to the home machine.

### Covers: playlists never over the internet (owner's rule, 2026-11-06)

A playlist's picked cover travels only over the home network or a VPN. A mood's cover, a user's own
or a global mood's, may go over the internet. MDHS listens only on the home network and the VPN, so
this is about MDW, which decides by the address a connection came in on, never by a header.

Over the internet, MDW:

- **Never asks for a playlist's picture**: it ignores the `cover` of a playlist PUT and doesn't answer
  `cover_missing`. Leina Player uploads a picture only after that answer (`putWithCover()`), and
  playlist and mood pictures share one endpoint, so this answer is what keeps the picture on the phone.
- **Leaves `cover` out of the playlists it sends**: the phone changes a playlist's cover only when
  the key is there, so it keeps its own, and no phone needs changing for this.
- **Refuses to serve a picture only playlists refer to**. A picture a mood also uses is served.

A phone reaching the same MDW over the VPN gets today's behaviour. A PUT without `cover` keeps the
stored one, so syncing over the internet doesn't lose it. The contract suite gets a case for each
point. This comes from reading the app's code; it hasn't been tried on a phone yet.

### Creating accounts

Leina Player can only log in (`POST /login`). MDW gets registration with **one-time invite codes**,
made by the owner in MDW's questionary UI: open registration would let anyone on the internet create
accounts and fill the disk. The phone's "Create account" screen needs a hand-off to MusicDatabaseApp
and an ADR there, as a new way of talking to the server. It appears only when the server says it
supports registration, because MDHS's server won't.

### Technology

- **FastAPI + Pydantic + uvicorn** for the server, **questionary** for the UI. MDHS's standard-library
  server stays as it is (the owner rejected FastAPI for that one).
- Deployment: a systemd user unit. Settings in `.env`.
- FastAPI's defaults differ from what the phone relies on, so each of these is a requirement with a
  contract test:
  - **Token before body**: FastAPI reads a declared body before it resolves dependencies, so the
    token check and size limit belong in middleware.
  - **Unknown fields**: Pydantic drops them by default, while sync stores data verbatim (`extra`,
    folder items, saved songs). Those models use `extra="allow"`.
  - **Error codes**: FastAPI answers 422 for a bad body, and Leina Player treats an unknown code as
    a failure to retry. Validation errors map to today's answers (400, 411, 413).
  - **Carried over from today's server**: the Host check (421), the login lockout, the upload limits,
    the 64-connection cap, the 60 s idle timeout and HTTPS with its own pinned certificate.
  - **Off on a public server**: `/docs`, `/redoc` and `/openapi.json`.

### Owner's rule: control over every import

- **Every third-party package is approved by the owner before it's added.** This covers the direct
  ones and, through the lock file, every transitive one. Each comes with a written reason in a
  `DEPENDENCIES.md` (what it's for, licence, why the standard library won't do) and is pinned to an
  exact version. A new transitive dependency after an upgrade is an approval too.
- **The standard library first.** A package is added only when the standard library has no reasonable
  way to do it.
- **Enforced by a test, not by memory.** A test walks MDW's source and fails on any import outside an
  allow-list: the standard library, the core, and the approved packages.
- **The core stays standard-library only**, enforced the same way.
- **Licences**: GPL-compatible only (MIT, BSD, Apache 2.0, LGPL, GPLv2-or-later, GPLv3).
- **Upgrades** are deliberate: no automatic updates. An upgrade lists what changed in the lock file
  for the owner to approve.

To approve before the first install: `fastapi`, `pydantic`, `uvicorn` and `questionary` (already in
MDHS), with the full transitive list and licences prepared at that point.

### Phases

1. **Extract the core** in this repo. No behaviour change; the existing tests must pass unchanged.
   The server's systemd unit may need reinstalling because of the moved files, which is recorded in
   the server's system docs.
2. **A contract test suite** for the sharing API: every endpoint, status, header and JSON shape
   Leina Player relies on (the app's ADRs and `sharing_server.py`'s docstring). Run against today's
   server first; it becomes the specification.
3. **MDW's base**: FastAPI passing the contract suite, accounts in questionary, the catalog from
   GitHub, sync, the inbox. The import allow-list test exists from its first commit.
4. **Request vetting**, as above.
5. **The exchange with MDHS**: fetching request packages, replaying song id changes in MDW's user
   databases, sending playlists.
6. **Registration**: invite codes and the hand-off to the app.
7. **The trial**, below.
8. **The decision about a VPS.** Hosting is decided only after MDW works.
9. **Splitting the duties**, only when the owner agrees to it.
10. **Modules**: streaming and others, when needed.

### The trial: MDHS keeps working as it does today

Until the owner agrees to split the duties (phase 9), MDHS keeps doing everything it does now, and
its server keeps serving the same phones and accounts.

- **MDW runs beside it**: its own port, data folder, certificate and test accounts. No real account
  exists on both, because the same account on two servers would drift apart.
- **MDHS keeps handling requests by itself**: it receives, checks and settles them as it does now.
  The way its real requests flow doesn't change during the trial.
- **Working with MDW is a separate test function beside that**: run by hand from MDHS, off by
  default, and separate from the real inbox, so test data doesn't reach the catalog.
- **A change to the API goes to both servers**, with the contract suite proving it.
- **The core extraction is the one change that touches MDHS during this time.** Its behaviour
  doesn't change, and it happens before the trial starts.
- **No fixed end date.** The trial is ready to end when two phones sync through MDW, a test request
  reaches the owner's review, a correction comes back to a phone, and the server survives restarts
  and bad input.

The split is done one duty at a time, each on the owner's word. Each step can be undone until real
data has been written on MDW: from then on, going back means moving that data.

## Consequences

- One more program to release and keep running. The core keeps shared logic in one place, but MDW's
  own parts are new code to maintain.
- Two servers implement one API, so a change means the contract suite, the core and both servers.
  This is the price of FastAPI over reusing today's server; they may converge later, as a separate
  decision.
- Friends' data lives on MDW and needs backups of its own once real accounts are there.
- MDW's catalog is the public copy, which leaves out `files.db`. Streaming from MDW needs the library
  module.

## Open questions (for the owner)

- How MDHS connects to MDW for the exchange: an owner-only endpoint or SSH. Decided in phase 5; on a
  VPS it shouldn't be reachable from the public internet.
- A web page for user administration, or questionary only?
- Whether the owner's own account ever moves to MDW. Not planned: it stays on MDHS.
