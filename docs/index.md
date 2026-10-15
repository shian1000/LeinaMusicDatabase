# Documentation Index

Entry point for humans and AI agents browsing this repo's documentation. If you're an AI agent
looking for rules, commands, and safety boundaries, start at [`AGENTS.md`](../AGENTS.md) instead —
this index is the map of everything else.

## Start here

- [Architecture](architecture.md) — module layout, dependency direction, how the phone client
  uses the data today and where a future API would sit.
- [Data model](data-model.md) — the two SQLite databases, their schemas (including albums and where cover images live), and identifier policy.
- [System registry](systems/index.md) — which subsystem owns which feature, and how they depend on
  each other.
- [Runbooks](runbooks/) — operational procedures. Currently just
  [database backup, restore, migrations, and sharing with the mobile app](runbooks/database.md).

## Durable records

- [`agent-notes/`](agent-notes/) — deep, subsystem-specific rationale, tradeoffs, and gotchas for
  AI agents. One file per subsystem; indexed under "Topic notes" in `AGENTS.md`. Current behavior
  belongs in `architecture.md` / `data-model.md` / the system registry; *why* it works that way, or
  a bug it fixes, belongs here.
- [`runbooks/`](runbooks/) — step-by-step operational procedures (backup/restore, adding a
  migration). How to *do* something safely, as opposed to `agent-notes/`'s *why it works this way*.
- [`decisions/`](decisions/) — architectural decision records (ADRs), for choices that affect
  several systems or would be expensive to reverse:
  [ADR-0001](decisions/0001-ui-independent-business-logic.md) (keep business logic out of
  `src/menu/`; add a future API as a sibling, not a rewrite of it) and
  [ADR-0002](decisions/0002-multiple-artists-per-song.md) (multiple artists per song via the
  `additional_song_artists` link table, `songs.artist_id` kept as the primary; why not extra
  columns or comma-separated ids),
  [ADR-0003](decisions/0003-phone-client-fills-gaps.md) (the MusicDatabaseApp phone client may
  fill missing data locally, MusicDatabase's values win, phone-found data will come back for
  review), and [ADR-0004](decisions/0004-per-user-data.md) (per-user private data: one `user.db`
  per user on T7, username + password, a review inbox planned for phone-found global data).
  Don't write one for an ordinary refactor.

## Temporary / point-in-time records

- [`handoffs/`](handoffs/) — notes for an agent working in another repo (passed on by the owner),
  e.g. [the inbox contract for MusicDatabaseApp](handoffs/inbox-for-musicdatabaseapp.md) and
  [the playlist sync contract](handoffs/playlists-for-musicdatabaseapp.md) and
  [the moods contract](handoffs/moods-for-musicdatabaseapp.md) and
  [links found for songs without one](handoffs/found-links-for-musicdatabaseapp.md) and
  [the videos found bad, in music.db](handoffs/bad-videos-for-musicdatabaseapp.md) and
  [the library's audio files at `/library/<id>`](handoffs/server-files-for-musicdatabaseapp.md) and
  [tracks removed from imported playlists](handoffs/excluded-tracks-for-musicdatabaseapp.md) and
  [YouTube links set by hand, music videos](handoffs/youtube-links-by-hand-for-musicdatabaseapp.md). Once
  the other side has acted on one, it can be deleted; the lasting facts live in the runbook.

Nothing else yet. If a future task needs a `plans/` (proposed work not yet done) or `reviews/`
(point-in-time investigation whose conclusions stay useful) entry, create the directory then — once
work ships, fold the stable result into the pages above rather than leaving it stranded in a plan.
