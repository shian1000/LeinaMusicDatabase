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
  - `src/utils/database/`: database access and session management. `database_getter.get_artists_from_db_session(category, query)` does a SQL substring `contains()` search on `query` (not an exact match) and returns results **ordered alphabetically by name**, not by best match — `results[0]` is whichever matching name sorts first, not the closest match. Never call it with an already-resolved artist's name just to re-fetch that same object (e.g. right after an exact-match query or a similarity-based match already found it) — the substring search can silently return a *different* artist whose name happens to contain the query and sorts earlier alphabetically. This is exactly how an exact-matched artist named "Sting" got silently resolved to "POLKADOT STINGRAY" in `import_data_from_mp3_tags.resolve_artist()`. If you already have the matched `Artist` object, use it directly instead of re-querying.
  - `src/utils/discoveries/`: metadata fetchers/import logic.
    - `discoveries_manager.py` loads every module in `discovery_modules/` via `sorted(glob("*.py"))` and queries them **in that sort order**. The files are deliberately named with numeric prefixes (`1music_brainz_fetcher.py`, `2wikipedia_fetcher.py`, `3google_search_fetcher.py`, `4itunes_fetcher.py`, `5genius_fetcher.py`) to pin fetch priority. If you add, rename, or remove a fetcher, keep the numeric prefix scheme consistent or you will silently change lookup order/priority.
    - `3google_search_fetcher.py` writes the raw HTML response to `debug.html` in the repo root for debugging. That file is a large, disposable scrape dump, not documentation — don't read it for context and don't hand-edit it. (It is currently git-tracked, unlike `debug.log`, which appears to be a gitignore oversight — flag to the user before deleting it.)
  - `src/utils/common/normalizer.py`: the **single, centralized** string-normalization implementation (`normalize()`, `compare()` — handles diacritics, Unicode scripts, apostrophes, punctuation). `text_utils.normalize_text()` and other call sites delegate to it. Do not write a new ad-hoc normalize/compare function elsewhere; extend this one.
  - `src/utils/common/text_utils.py` similarity functions: `similarity(a, b)` is the plain two-string comparator (0–1 ratio, backed by `difflib.SequenceMatcher`) — use it directly when comparing two raw strings. `are_song_entries_similar(db_object, title_query, artist_query, threshold)` and `are_artists_entries_similar(db_object, artist_query, threshold)` are *not* alternate signatures for the same thing — they require an actual DB object (`.title`/`.artist.name`/`.name`) as the first argument, not a second plain string. Passing two plain strings to either raises a `TypeError` (this broke `discovery_modules/5genius_fetcher.py`'s `title_matches_url()` until fixed). If you're comparing two strings, call `similarity()`; only use the `are_*_entries_similar` helpers when you actually have a DB row to compare a query against.
    - `SequenceMatcher`'s ratio is unreliable on short strings: two different names sharing just a first letter and a common suffix can still score high (e.g. `similarity("A.Mia", "Armia")` is 0.8) purely because most of the characters happen to line up in a short string. When comparing a similarity score against a match threshold for artist names, use `scaled_similarity_threshold(a, b, base_threshold)` instead of comparing against `base_threshold` directly — it raises the required ratio for short strings (≤5 chars → 0.92, ≤8 chars → 0.85) before falling back to `base_threshold` for longer names. `are_artists_entries_similar()` already applies this internally; `resolve_artist()` and `check_artist_spelling()` in `import_data_from_mp3_tags.py` call it explicitly at their fuzzy-match checks.
  - `src/utils/common/debug.py`: project logging convention — use `slog(var)` / `mlog(message)` instead of bare `print()` for diagnostics. Output is gated by a `.debug` file at repo root (`verbosity = N`); everything also appends to `debug.log` (gitignored) regardless of that gate.
  - `src/utils/youtube/`: YouTube playlist and single-video download helpers. `manage_youtube_playlists.search_video`/`search_video_ytdlp` pick a "best match" via `score_result(candidate_title, artist, title)`, which returns `(relevance, quality)` — `quality` is the old HQ/LQ/video-vs-audio keyword score, but `relevance` (a `similarity()` check) is what actually gates whether a candidate is accepted: a result must clear `MIN_RELEVANCE` (0.35) or the search returns no match instead of adding a wrong video. `relevance` deliberately compares the song **title only**, not artist+title combined, and strips bracketed annotations/hashtags from both sides first (`_relevance_text()`) — comparing the combined string let a wrong song by a same-named artist score high, and skipping the bracket/hashtag strip let two unrelated videos that both merely carried a shared caption like `[Save Ukraine - #StopWar]` look similar for the wrong reason. Keep both of those when touching this function.
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
