# Architecture

`main.py` starts an interactive terminal menu (`src/menu/`). It is a desktop app the user drives
directly — not a server, no request/response cycle.

## Dependency direction

```
main.py
  |
  v
src/menu/                         terminal UI: presentation + workflow wiring
  |
  v
src/utils/{database,discoveries,youtube,ui}/     orchestration for one subsystem each
  |
  v
src/utils/common/                 shared, dependency-free infra:
                                   normalizer, text_utils, debug,
                                   selenium_sessions, musicbrainz_client,
                                   spellcheck_cache
  |
  v
src/config/constants.py, src/settings.py         constants + env config, no inward dependencies
```

`src/menu/` is the only layer allowed to know about *all* the others — it's the orchestrator that,
for example, resolves a `Song` via `src/utils/database/`, then hands its artist/title to
`src/utils/youtube/` to find a matching video. `src/utils/youtube/` and `src/utils/discoveries/`
don't import each other or the database layer directly for search purposes; `discoveries/` is the
one exception that does read/write the database directly (`import_data_from_mp3_tags.py` creates
`Artist`/`Song` rows during import) because import *is* a database-writing workflow, not a search.

See `AGENTS.md` → **Architecture boundaries** for the enforced rules (SQL only in
`src/utils/database/`, one canonical implementation per cross-cutting concern, etc.) and the
**Module map** for what each package owns. This page is about layout and direction; that one is
about what's not allowed to move.

## Where a future API would sit

No HTTP API or mobile client exists yet. If one is built, it should be a new sibling of
`src/menu/` — calling into the same `src/utils/*` orchestration layer rather than the menu layer
being repurposed as a backend. `src/menu/`'s job is terminal presentation and prompt flow; an API
adapter's job would be request/response and serialization. Neither should own business rules that
belong in `src/utils/*`. This is a recorded decision, not just a convention:
[ADR-0001](decisions/0001-ui-independent-business-logic.md) has the full reasoning, including why
a full `domain/`/`application/` layer split isn't happening now.

Before building that adapter, decide and document (a decision record under `docs/decisions/` is
the right place once this becomes real):

- whether the database stays local-per-device, becomes centrally hosted, or gets synchronized;
- authentication and data ownership;
- offline behavior and conflict resolution;
- pagination/search response contracts (the existing category system in
  `docs/agent-notes/database-search.md` is the natural basis for a search endpoint's contract).

## Why two SQLite databases instead of one

`music.db` (catalog) and `tag.db` (tags) are separate SQLite files, each with its own SQLAlchemy
`Base`/engine/session. See [`data-model.md`](data-model.md) for the schema and the consequence
(cross-database references are plain integers, not enforced foreign keys).
