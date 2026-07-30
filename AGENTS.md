# AGENTS.md

This file is the single source of truth for AI agents working in this repository.

- Project purpose: manage and enrich a local music database from local files and external metadata sources.
- Entry point: run the app with `python main.py`.
- Main code: `main.py` starts the CLI; app logic lives under `src/menu/` and `src/utils/`.
- Important modules:
  - `src/settings.py`: runtime paths and environment-based config.
  - `src/utils/database/`: database access and session management.
  - `src/utils/discoveries/`: metadata fetchers/import logic.
  - `src/utils/youtube/`: YouTube playlist and single-video download helpers.
  - `tests/`: regression tests.
- Setup:
  - `python3 -m venv venv`
  - `source venv/bin/activate`
  - `pip install -r requirements.txt`
- When modifying code:
  - Prefer small, focused changes in existing modules.
  - Preserve current CLI flow unless the task explicitly requires a redesign.
  - If a change affects workflow, setup, entry points, or architecture, update this file briefly.
- Verification:
  - Run the relevant test or command before claiming success.
  - Before creating or adding regression tests, ask the user for confirmation unless the request explicitly requires test changes.
- Performance notes:
  - The expensive bottleneck is not the local DB comparison itself; it is the MusicBrainz network call used by `check_spelling()`.
  - The import path optimization intentionally tries the local database first and only falls back to MusicBrainz when no good local match exists.
  - The spell-check menu was still remote-first; it now applies the same DB-first shortcut before calling MusicBrainz, and keeps a small in-memory cache to avoid repeated network lookups within a session.
  - If a user is actively running spell checking, avoid running the whole test suite while the live work is in progress unless they explicitly ask for it.
