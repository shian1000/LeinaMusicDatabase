# tests/

Run everything: `python -m pytest` (config in `pyproject.toml` — `testpaths = ["tests"]`, so this
works from the repo root with no extra flags). Run one file: `python -m pytest tests/<file>.py`.

Currently 127 tests, all fully mocked — no live network calls, no scraping, no real MusicBrainz/
YouTube/Wikipedia traffic. Full run takes ~2 seconds. (`AGENTS.md` used to say several of these
hit real network — that was true once, but every test here is mocked as of 2026-09; verified by
reading each file, not assumed.)

## What each file covers

| File | Covers |
| --- | --- |
| `test_mp3_metadata_fallback.py` | `mp3_utils.extract_metadata_with_fallback()` — falling back to filename parsing when ID3 tags are missing. |
| `test_wikipedia_fetcher.py` | `wikipedia_fetcher.get_album_name()` — returns `None` (not a crash) when Wikipedia's API returns undecodable JSON. |
| `test_youtube_download.py` | `enter_database.download_yt_song_menu()` — the prompt-for-a-link download flow, with the actual download and `questionary` prompt mocked out. |
| `test_youtube_search.py` | `manage_youtube_playlists.score_result()` / `search_video_ytdlp()` — the YouTube match-ranking heuristic. 96 tests: most are individually-named tests pinned to one specific real-world song each (a regression catalog of past wrong-match bugs), plus two mirrored parametrized suites re-running the same ~29 real songs end-to-end — `test_regression_suite_resolves_via_fresh_search` (no DB involved) and `test_regression_suite_resolves_via_db_reference` (via `Song.youtube_video_id`) — see `docs/agent-notes/youtube-search-matching.md` for the full list and the reasoning behind the heuristic. |
| `test_scripts.py` | Nothing — explicit scratch/manual file, excluded from collection via `conftest.py` (see there for why). |
| `manual/` | Manual diagnostic/timing scripts, not automated tests — real imports and live network, excluded from collection via `manual/conftest.py`. Run individually, e.g. `python tests/manual/run_import_test.py`. |

## Keeping this catalog honest

A file's entry above going stale (function renamed/removed, behavior changed) is exactly the kind
of drift that let `test_import_mp3.py` sit broken and uncollected for ~3.5 months before anyone
noticed (deleted 2026-09 — it tested `upsert_song()` / `apply_tag()` / `_find_exact_match()`, all
removed in a refactor that never touched the tests). When a change to `src/utils/discoveries/` or
`src/utils/youtube/` renames or removes something a test here covers, update this table and the
test in the same change — don't leave either to rot separately.
