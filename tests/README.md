# tests/

Run everything: `python -m pytest` (config in `pyproject.toml` — `testpaths = ["tests"]`, so this
works from the repo root with no extra flags). Run one file: `python -m pytest tests/<file>.py`.

Currently 273 tests, all fully mocked — no live network calls, no scraping, no real MusicBrainz/
YouTube/Wikipedia/Spotify traffic. Full run takes ~2-3 seconds. (`AGENTS.md` used to say several of
these hit real network — that was true once, but every test here is mocked as of 2026-09; verified
by reading each file, not assumed.)

## What each file covers

| File | Covers |
| --- | --- |
| `test_mp3_metadata_fallback.py` | `mp3_utils.extract_metadata_with_fallback()` — falling back to filename parsing when ID3 tags are missing. |
| `test_discovery_stats.py` | `discovery_stats.record_invocation()` / `record_success()` — persisted per-module counters, including the in-memory cache surviving a reload — plus `discoveries_manager._call_module()` / `discover_album_name()` recording an invocation on every call and a success only when the result survives `_validate_result()` (crash, empty/blacklisted, and mismatched-similarity cases each verified). |
| `test_album_discovery_queries.py` | Regression catalog of real songs whose DB title/artist confused the album fetchers: pins the first (artist, title) query `discoveries_manager.discover_album_name()` sends to a fetcher (i.e. what `truncate_at_word()` + parenthesis-stripping produce), e.g. the Polish `"- gościnnie X"` guest credit. Add a new problematic song as one more row in `ALBUM_QUERY_REGRESSION_CASES`. Also `ALBUM_EMPTY_FETCHERS_CASES`: when every fetcher comes up empty, a soundtrack-like artist (`text_utils.is_soundtrack_artist()`, e.g. "Tekken 5 OST") becomes the album, while "Ghost"/"Post Malone" don't, and a cover/unplugged version (`text_utils.has_singles_marker()`, a `SINGLES_MARKER` word such as "cover"/"unplugged" as a whole word in title or artist — not "Recover") is filed under `SINGLES_ALBUM` ("Singles"); a real fetcher result still wins over both fallbacks. |
| `test_wikipedia_fetcher.py` | `wikipedia_fetcher.get_album_name()` — returns `None` (not a crash) when Wikipedia's API returns undecodable JSON. |
| `test_spotify_fetcher.py` | `spotify_fetcher._find_matching_track()` / `_extract_album_from_track_page()` — the search-results row matching and track-page album scraping, against static HTML fixtures (no Selenium, no live `open.spotify.com` traffic). |
| `test_itunes_fetcher.py` | `itunes_fetcher._find_song_link()` (both known search-results layouts, see `docs/agent-notes/discovery-modules.md`) and `extract_from_itunes_soup()` — artist/title verification, the "(feat. X)" title-credit cleanup, and the `" - Single"` → `"Singles"` album rewrite — against static HTML fixtures (no Selenium, no live `music.apple.com` traffic). |
| `test_genius_fetcher.py` | `genius_fetcher.title_matches_url()` — a search-result URL is matched on the slug with the artist stripped, so a short title by a long-named artist ("Childish Gambino - Cruisin'") isn't rejected; a different song or the same title by another artist still is. |
| `test_youtube_fetcher.py` | `youtube_fetcher._find_matching_song()` — matching a filtered "Songs" search-results row (title/artist/album all read from the row itself, no track page to follow), including multi-artist rows, a collab stored in the DB as one joined artist ("Sw@da x Maxim x Niczos" vs. three separate artist links), and rows missing an album link, against static HTML fixtures (no Selenium, no live `music.youtube.com` traffic). |
| `test_youtube_download.py` | `enter_database.download_yt_song_menu()` — the prompt-for-a-link download flow, with the actual download and `questionary` prompt mocked out. |
| `test_manage_youtube_playlists.py` | `manage_youtube_playlists.create_yt_playlist()`'s DB-link reuse/validate/persist/clear flow (`is_video_id_valid()`, `save_video_id_to_song()`, `NO_VIDEO_SENTINEL` ("N/A") handling), plus `add_video_to_playlist()`'s quota-exceeded propagation. |
| `test_yt_cache.py` | `yt_cache.init_cache()` / `make_song_key()` — pre-filling a song's cache entry from `Song.youtube_video_id` (including a manual override), and disk cache load/save/clear. |
| `test_youtube_search.py` | `manage_youtube_playlists.score_result()` / `search_video_ytdlp()` — the YouTube match-ranking heuristic. 117 tests: most are individually-named tests pinned to one specific real-world song each (a regression catalog of past wrong-match bugs), plus two mirrored parametrized suites re-running the same ~36 real songs end-to-end — `test_regression_suite_resolves_via_fresh_search` (no DB involved) and `test_regression_suite_resolves_via_db_reference` (via `Song.youtube_video_id`) — see `docs/agent-notes/youtube-search-matching.md` for the full list and the reasoning behind the heuristic. |
| `test_import_from_playlist.py` | `import_from_playlist._resolve_artist_synonyms()` — rewriting a parsed playlist entry's artist name to its canonical DB name on an exact (case-insensitive) match against a known `synonyms` entry, before the entry is ever shown to the user. |
| `test_resolve_duplicates.py` | `resolve_duplicates.remove_duplicate_artists()` — merging an artist whose plain name exactly matches a different artist's `synonyms` entry into the synonym-carrying artist (songs reassigned, duplicate row deleted), alongside the pre-existing same-name dedup. Uses an in-memory SQLite DB rather than mocking the session; see `AGENTS.md`'s `src/menu/` entry for the `importlib` workaround this file needs to reach the submodule. |
| `test_song_artists.py` | Multi-artist songs (`additional_song_artists`): `song_artists.py` labels ("A x B feat. C", extras sorted by name), add/remove/role validation, song-delete cascade, the DB search filters (`SongSearchFilters.artist_name` / `general_search` / `artist_id`) and `count_artist_songs()` reaching additional artists, `reassign_additional_artist_links()` dropping links that would duplicate after an artist merge, `resolve_duplicates.remove_duplicate_artists()` carrying links over, and migration 0003 matching the model and being re-runnable. Also the fetcher/YouTube side: `discoveries_manager` querying the primary artist first then the "A x B" label and accepting a matched field that credits the whole collab, `score_result()`'s `main_artists` (all required, synonyms count, regex split off - "Final Fantasy X OST") / `feat_artists` (bonus only), and `yt_cache.init_cache()` storing the label + credits under the primary-artist key. In-memory SQLite. |
| `test_artist_splitting.py` | Splitting joined artist names: `artist_splitting.propose_artist_split()` (a table of real DB names — feat./ft./x/","/"&"/slashes split, "Final Fantasy X OST"/"AC/DC"/"Malcolm X" not, which ones are pre-selected), `find_split_candidates()` (existing parts reused, ignore list, artists without songs skipped), `apply_artist_split()` (songs moved, new parts inherit origin, joined row deleted, one new artist shared across a batch, joined artist used as someone's additional artist replaced by its parts) and `review_artist_splits()` (ticked names applied, the rest remembered). In-memory SQLite. |
| `test_scripts.py` | Nothing — explicit scratch/manual file, excluded from collection via `conftest.py` (see there for why). |
| `manual/` | Manual diagnostic/timing scripts, not automated tests — real imports and live network, excluded from collection via `manual/conftest.py`. Run individually, e.g. `python tests/manual/run_import_test.py`. |

## Keeping this catalog honest

A file's entry above going stale (function renamed/removed, behavior changed) is exactly the kind
of drift that let `test_import_mp3.py` sit broken and uncollected for ~3.5 months before anyone
noticed (deleted 2026-09 — it tested `upsert_song()` / `apply_tag()` / `_find_exact_match()`, all
removed in a refactor that never touched the tests). When a change to `src/utils/discoveries/` or
`src/utils/youtube/` renames or removes something a test here covers, update this table and the
test in the same change — don't leave either to rot separately.
