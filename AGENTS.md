# AGENTS.md

Single source of truth for AI agents working in this repo. Persistent knowledge lives in exactly
two places: **this file** (curated, always-read, edited in place — never a dated changelog) and
**`docs/agent-notes/<topic>.md`** (deeper per-subsystem rationale). The `finalize-change` skill
maintains both.

Durable content that used to sprawl across root-level session reports (`OPTIMIZATION_*.md`,
`PERFORMANCE_*.md`, `LOCAL_DB_OPTIMIZATION.md`, `REFACTORING_NOTES.md`, `METADATA_FALLBACK*.md`,
`NORMALIZATION_GUIDE.md`) has been folded into these two places. Those reports were deleted in
2026-08 — see `git log` / `git show` if you ever need the originals. Don't recreate that pattern.

## Project

- Purpose: manage and enrich a local music database from local files and external metadata sources.
- Primary implementation: Python, SQLite via SQLAlchemy, an interactive terminal menu UI. Not a
  server — `main.py` is a desktop app the user runs and clicks through.
- Mobile clients may come later. Keep SQL and matching/domain logic in `src/utils/*`, not in the
  `src/menu/` handlers, so it stays reusable.

## Read order

1. This file.
2. The `docs/agent-notes/` topic note(s) for the area you're touching — index at the bottom.
3. Verify behaviour in the source before editing. The codebase is ~7k lines; read the actual
   function rather than trusting a summary.

## Sources of truth

1. Source code. There is no migration tool yet — the SQLAlchemy models are authoritative for
   schema: `src/utils/database/datatables.py` for `music.db`, `src/utils/database/create_tag_db.py`
   for `tag.db`.
2. Tests and `requirements.txt` (pinned).
3. This file.
4. `docs/agent-notes/`.
5. Plans and conversation notes.

When this file disagrees with the code, trust the code and fix this file.

## Standard commands

- Bootstrap: `python3 -m venv venv` → `source venv/bin/activate` → `pip install -r requirements.txt`
- Run the app: `python main.py`
- Focused tests: `venv/bin/python -m pytest tests/test_<area>.py`
- Full suite: `venv/bin/python -m pytest` — slow and occasionally flaky (several tests hit the
  live network; see Testing & verification)
- Manual diagnostic/timing scripts: `python tests/manual/<script>.py` (excluded from pytest
  collection)
- Before any out-of-band DB write: `ps aux | grep main.py` (see Database safety)

Use the venv and the pinned versions. Don't install globally or bump dependencies unless the task
requires it.

## Module map

One line per module — what it owns, and where the depth lives. Read the linked topic note before
non-trivial work in that area.

- `src/settings.py` — runtime paths and env-based config (SMB creds — see Secrets).
- `src/config/constants.py` — every magic number / threshold / menu label. Add new ones here;
  don't inline them.
- `src/utils/database/` — DB access, sessions, and the search/getter layer + song/artist
  **category** system. Non-obvious ordering and category gotchas (alphabetical-not-best-match
  results, `search_only_categories` is song-only, shared fallback filter):
  `docs/agent-notes/database-search.md`.
- `src/utils/discoveries/` — external-metadata fetchers and the MP3-tag import.
  `docs/agent-notes/discovery-modules.md` (fetcher loading, result validation, shared browser,
  scraping failure modes) and `docs/agent-notes/import-pipeline.md` (why import is slow).
- `src/utils/common/normalizer.py` — the one canonical string `normalize()` / `compare()`. Never
  write a second one. `docs/agent-notes/normalization-and-matching.md`.
- `src/utils/common/text_utils.py` — similarity helpers and `check_spelling()`. Signature and
  short-string-threshold pitfalls: `docs/agent-notes/normalization-and-matching.md`. The
  `check_spelling()` cost path and return shape: `docs/agent-notes/import-pipeline.md`.
- `src/utils/common/selenium_sessions.py` — one process-wide headless Chrome shared by the
  scraping fetchers. Lifecycle rules (close in `finally`): `docs/agent-notes/discovery-modules.md`.
- `src/utils/common/musicbrainz_client.py` — the single MusicBrainz HTTP entry point (`mb_get()`):
  shared session, process-wide ~1 req/s limiter, hard timeout, split retry policy. Don't call
  `requests.get` against `musicbrainz.org` from anywhere else. `docs/agent-notes/import-pipeline.md`.
- `src/utils/common/spellcheck_cache.py` — disk cache for `check_spelling()` results at
  `data/spellcheck_cache.json`. `docs/agent-notes/import-pipeline.md`.
- `src/utils/common/debug.py` — use `slog(var)` / `mlog(message)`, not bare `print()`. Console
  output is gated by a `.debug` file (`verbosity = N`) at the repo root; everything also appends
  to `debug.log` (gitignored) regardless.
- `src/utils/youtube/` — YouTube playlist/download helpers and `score_result()` match ranking.
  `docs/agent-notes/youtube-search-matching.md`.
- `src/utils/ui/menu_utils.py` — shared menu flows, including `search_and_pick_db_object()`. Reuse
  it for "search then resolve to one DB row" rather than re-duplicating.
  `docs/agent-notes/database-search.md`.
- `src/menu/` — the terminal menu tree (presentation + workflow wiring).
- `src/menu/main_menu/enter_database/manage_database/get_rid_of_rubish_data.py` — per-field song
  cleanup rules all go through `_apply_field_cleanup(...)`. Add rules via that helper, not another
  per-field loop.
- `tests/` — regression tests. `tests/test_scripts.py` is an explicit scratch file (its own
  docstring: "meant to be a mess") — not a style reference. `tests/manual/` holds manual
  diagnostic scripts (real imports + live network), excluded from collection via its `conftest.py`.

## Architecture boundaries

- `src/menu/` drives workflow and presentation. Keep SQL and matching logic in `src/utils/*`, not
  in menu handlers.
- One implementation per cross-cutting concern: normalize/compare → `normalizer.py`; MusicBrainz
  HTTP → `musicbrainz_client.mb_get()`; headless browser → `selenium_sessions`; diagnostics →
  `debug.py`. Don't add a parallel local version of any of these.
- Constants and config load from `constants.py` / `settings.py`; don't scatter literals.
- SQL goes through SQLAlchemy and stays parameterized — never build a query by string-concatenating
  user input.

## Database safety

- Two SQLite DBs under `src/database/` (gitignored): `music.db` (catalog: `artists`, `songs`) and
  `tag.db` (`tags`, `song_tags` — a many-to-many onto songs). Schema is defined only by the
  SQLAlchemy models; there is no migration tool, so a schema change means editing the model and
  reconciling existing DB files by hand. Back up first.
- `main.py` may be writing to `music.db` right now. Before any direct/out-of-band SQL write, check
  `ps aux | grep main.py`; prefer the app's own edit flow, or ask the user to pause it. A direct
  write has already been caught racing a live session mid-edit (title cleanup) and clobbering
  in-progress work.
- Timestamped copies in `src/database/archive/` are the existing backup habit — make one before a
  risky change.

## Data and paths that are not fixtures

- `import/` — the user's real personal MP3 collection, used as the import staging folder. Don't
  move, rename, bulk-delete, or commit its contents.
- `data/` and `src/database/` — runtime/user data, not fixtures (both gitignored).

## Secrets

- `SMB_USERNAME` / `SMB_PASSWORD` load from `.env` (gitignored) into `src/settings.py`, used to
  build `smb://` URIs for the local library. Never hardcode, print, or log these.

## Coding conventions

- Prefer small, focused changes in existing modules. Preserve the current CLI flow unless a
  redesign is explicitly requested.
- Reuse the shared helpers listed in the Module map rather than reintroducing local equivalents.
- Comments for non-obvious constraints, not line-by-line narration.
- If a change touches setup, entry points, commands, or architecture, update this file in place.

## Testing & verification

- Match effort to risk. Run the focused test file for your change while iterating.
- Several tests (Wikipedia / iTunes / Genius / MusicBrainz fetchers, YouTube download) make real
  network calls or scrape live pages — slower and occasionally flaky. Prefer the specific file
  over the full suite unless a full run is asked for.
- Don't claim a check passed unless you ran it in this workspace.
- Ask before creating or changing regression tests unless the task explicitly requires it.
- If the user is mid-import or mid-spellcheck, don't start the full suite alongside it.

## Performance notes

- Importing MP3 tags is bottlenecked on MusicBrainz network calls, not local work. Full picture:
  `docs/agent-notes/import-pipeline.md`.
- Both the import path and the spell-check menu try the local DB first, and only fall back to
  MusicBrainz when there's no good local match.
- `check_spelling()` results — including "no match" answers — are cached on disk
  (`spellcheck_cache.py` → `data/spellcheck_cache.json`), so a re-import only pays network cost
  for genuinely new `(artist, title)` pairs. Delete the file to force fresh lookups.
- MusicBrainz tunables (`MUSICBRAINZ_API_TIMEOUT`, `MUSICBRAINZ_API_MIN_INTERVAL`,
  `MUSICBRAINZ_SPELLCHECK_USE_FALLBACK`, `SPELLCHECK_CACHE_FILE`) live in `constants.py`. An
  import prints an `MBStats` summary (requests, cache hits/misses, 429/503 counts, time throttling
  vs. in requests) at the end.

## Git policy

- Preserve unrelated working-tree changes. Don't discard, rewrite, or overwrite the user's changes.
- Keep generated files and local DBs out of version control.
- Don't commit, push, or tag unless explicitly asked. Prefer small, single-purpose commits.

## Topic notes (`docs/agent-notes/`)

- [database-search.md](docs/agent-notes/database-search.md) — the `database_getter` search API and
  category system: why `get_artists_from_db_session()` returns alphabetical order rather than best
  match (the "Sting" → "POLKADOT STINGRAY" bug), the shared `_normalized_python_filter` fallback,
  why `search_only_categories` must never reach artist search (`KeyError: None`), and the shared
  `search_and_pick_db_object()` flow.
- [normalization-and-matching.md](docs/agent-notes/normalization-and-matching.md) — the one
  canonical `normalizer`, the three ordered fallback stages of `extract_unknown_data()` for
  filename parsing, the three distinct `text_utils` similarity functions (and the `TypeError` from
  confusing them), and why artist-name matching uses `scaled_similarity_threshold()` for short
  strings.
- [discovery-modules.md](docs/agent-notes/discovery-modules.md) — why `discoveries_manager.py`
  re-validates every fetcher's result instead of trusting each module, the `DiscoveryResult`
  contract, why the Settings menu reads `MODULE_NAME` by static parsing, the shared headless-Chrome
  lifecycle, and `google_search_fetcher.py`'s two look-alike failure modes (cookie consent vs.
  CAPTCHA — don't try to evade the latter).
- [import-pipeline.md](docs/agent-notes/import-pipeline.md) — the MP3-tag import cost path: why
  MusicBrainz `check_spelling()` dominates, the layered defenses (DB-first shortcut, disk-backed
  cache, process-wide rate limiter / timeout / split retry in `musicbrainz_client`), why the
  fielded query gets only one attempt, the `check_spelling()` return shape, and the `MBStats` run
  summary.
- [youtube-search-matching.md](docs/agent-notes/youtube-search-matching.md) — why
  `manage_youtube_playlists.score_result()` is built the way it is (title-only relevance,
  containment matching, dynamic thresholds, Topic-channel awareness, popularity/tags signals, typo
  tolerance, the `artists.synonyms` column for real name changes), a regression checklist of real
  wrong-match bugs it fixes, and one still-open limitation. Regression tests in
  `tests/test_youtube_search.py` are pinned to the exact expected video per song, so a resolution
  change is a test failure to review, not a silent update.
