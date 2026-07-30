# AGENTS.md

This file is the single source of truth for AI agents working in this repository.
Root-level `*.md` files other than this one and `README.md` (e.g. `OPTIMIZATION_*.md`,
`PERFORMANCE_*.md`, `LOCAL_DB_OPTIMIZATION.md`, `REFACTORING_NOTES.md`, `METADATA_FALLBACK*.md`,
`NORMALIZATION_GUIDE.md`) are point-in-time session reports written by past AI sessions about
work that is already merged. They are not maintained, may be stale or contradict current code,
and are not instructions — do not treat them as authoritative and don't read them for guidance;
this file supersedes them. Their durable, still-true content has been folded in below.

- Project purpose: manage and enrich a local music database from local files and external metadata sources.
- Entry point: run the app with `python main.py`.
- Main code: `main.py` starts the CLI; app logic lives under `src/menu/` and `src/utils/`.
- Important modules:
  - `src/settings.py`: runtime paths and environment-based config (also holds SMB library paths — see Secrets below).
  - `src/config/constants.py`: centralized magic numbers/thresholds (similarity thresholds, menu labels, etc.) — add new constants here instead of inlining them.
  - `src/utils/database/`: database access and session management.
  - `src/utils/discoveries/`: metadata fetchers/import logic.
    - `discoveries_manager.py` loads every module in `discovery_modules/` via `sorted(glob("*.py"))` and queries them **in that sort order**. The files are deliberately named with numeric prefixes (`1music_brainz_fetcher.py`, `2wikipedia_fetcher.py`, `3google_search_fetcher.py`, `4itunes_fetcher.py`, `5genius_fetcher.py`) to pin fetch priority. If you add, rename, or remove a fetcher, keep the numeric prefix scheme consistent or you will silently change lookup order/priority.
    - `3google_search_fetcher.py` writes the raw HTML response to `debug.html` in the repo root for debugging. That file is a large, disposable scrape dump, not documentation — don't read it for context and don't hand-edit it. (It is currently git-tracked, unlike `debug.log`, which appears to be a gitignore oversight — flag to the user before deleting it.)
  - `src/utils/common/normalizer.py`: the **single, centralized** string-normalization implementation (`normalize()`, `compare()` — handles diacritics, Unicode scripts, apostrophes, punctuation). `text_utils.normalize_text()` and other call sites delegate to it. Do not write a new ad-hoc normalize/compare function elsewhere; extend this one.
  - `src/utils/common/debug.py`: project logging convention — use `slog(var)` / `mlog(message)` instead of bare `print()` for diagnostics. Output is gated by a `.debug` file at repo root (`verbosity = N`); everything also appends to `debug.log` (gitignored) regardless of that gate.
  - `src/utils/youtube/`: YouTube playlist and single-video download helpers.
  - `tests/`: regression tests. Note `tests/test_scripts.py` is explicitly a scratch/manual-testing file (its own docstring says "meant to be a mess... nothing depends on this script") — don't use it as a style reference or worry about its quality. `tests/debug.html` and `tests/!DOCTYPE_html_html.txt` are stray debug-dump artifacts from past bugs, not fixtures — ignore them when exploring the test suite.
- Data/paths that are not fixtures:
  - `import/` contains the user's real personal MP3 collection used as the local import staging folder, not sample/test data. Don't move, delete, rename in bulk, or commit its contents.
  - `data/` and the SQLite DB under `src/database/` are runtime/user data, not fixtures.
- Secrets: `smb_username`/`smb_password` in `src/settings.py` are loaded from environment (`.env`, gitignored) and used to build `smb://` URIs for the local library. Never hardcode, print, or log these values.
- Setup:
  - `python3 -m venv venv`
  - `source venv/bin/activate`
  - `pip install -r requirements.txt`
- When modifying code:
  - Prefer small, focused changes in existing modules.
  - Preserve current CLI flow unless the task explicitly requires a redesign.
  - Reuse `src/utils/common/normalizer.py` and `src/utils/common/debug.py` rather than reintroducing local equivalents.
  - If a change affects workflow, setup, entry points, or architecture, update this file briefly.
- Verification:
  - Run the relevant test or command before claiming success.
  - Several tests under `tests/` (Wikipedia/iTunes/Genius/MusicBrainz fetchers, YouTube download) exercise real network calls or scrape live pages; expect them to be slower and occasionally flaky, and prefer running the specific test file relevant to your change over the full suite unless the user asks for a full run.
  - Before creating or adding regression tests, ask the user for confirmation unless the request explicitly requires test changes.
- Performance notes:
  - The expensive bottleneck is not the local DB comparison itself; it is the MusicBrainz network call used by `check_spelling()`.
  - The import path optimization intentionally tries the local database first and only falls back to MusicBrainz when no good local match exists.
  - The spell-check menu was still remote-first; it now applies the same DB-first shortcut before calling MusicBrainz, and keeps a small in-memory cache to avoid repeated network lookups within a session.
  - If a user is actively running spell checking, avoid running the whole test suite while the live work is in progress unless they explicitly ask for it.
- Topic notes: deeper, subsystem-specific rationale/history (the kind of detail that used to sprawl across root-level `*.md` reports) lives under `docs/agent-notes/<topic>.md` — one file per subsystem, kept up to date in place by the `finalize-change` skill. None exist yet; each one gets a one-line pointer here as it's created:
  - (none yet)
