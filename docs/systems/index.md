# System Registry

One row per subsystem. "Docs" links to where the *depth* lives — a system without a dedicated
`agent-notes` file yet is documented inline in `AGENTS.md`'s Module map.

| System | Owns | Docs |
| --- | --- | --- |
| Catalog / DB search | `src/utils/database/` — storage for artists/songs/tags and albums (`albums.py`: `songs.album_id` kept in step with `songs.album`, cover rows), the search/getter API, the category system, backups/migrations, users and their private `user.db` (`users.py`, `user_playlists.py`), the music library index `files.db` mapping audio files to songs (`files_index.py`), Latin forms of non-Latin names and their "Fill in Latin forms" list (`latin_names.py`, `utils/ui/latin_forms_review.py`), songs' genres in one spelling per genre and their MusicBrainz lookup (`genres.py`, `utils/discoveries/genre_lookup.py`, `utils/ui/genre_review.py`), songs that are really whole albums (`songs.kind`: `song_kinds.py`, the "Find full albums" review `full_album_candidates.py` / `utils/ui/full_album_review.py`), keeping `songs.id` references in tag.db / files.db / users' DBs valid across merges and deletions (`song_references.py`), copying + serving the DBs, and the library's audio files by `files.id`, to the MusicDatabaseApp phone client (`sharing.py`, `sharing_server.py`; the step-by-step setup of this PC as the server in `utils/common/sharing_setup.py` + `utils/ui/sharing_wizard.py`), logging the phone in as a user (`account_auth.py`), syncing users' playlists, moods and Biblioteka folders with their phones (`playlist_sync.py`, `mood_sync.py`, `folder_sync.py`, shared by the server and the menu), their liked / "Na później" songs and saved links (`saved_sync.py`), storing their plays for Leina Radio's listening stats (`listen_sync.py`), global moods, promotion and the phones' logged changes to them (`moods.py`, `global_mood_log.py`), and the inbox of requests it sends back (`inbox_requests.py`, field corrections in `inbox_field_corrections.py`, links found for songs without one in `inbox_found_links.py`, songs it asks to have added in `inbox_song_requests.py`, the automatic pass in `inbox_auto.py` run every 15 min by a systemd timer via `auto_resolve_requests.py` and kept off an open app by `app_lock.py`, reviewed in `utils/ui/inbox_request_review.py`, request packages sent by hand imported in `request_packages.py`), and the public copy of the catalog published as a GitHub Release for phones without the server (`public_export.py` allow-list export + self-check, `public_database.py`, `github_release.py`) | [agent-notes/database-search.md](../agent-notes/database-search.md), [runbooks/database.md](../runbooks/database.md) |
| Discovery | `src/utils/discoveries/` — external metadata fetchers (MusicBrainz, Wikipedia, iTunes, Genius, Google, Spotify, YouTube), MP3-tag import, album covers from embedded mp3 pictures (`mp3_covers.py`), playlists from a folder of mp3s (`folder_playlist.py`) | [agent-notes/discovery-modules.md](../agent-notes/discovery-modules.md), [agent-notes/import-pipeline.md](../agent-notes/import-pipeline.md) |
| Normalization & matching | `src/utils/common/normalizer.py`, `text_utils.py` — canonical string normalization and fuzzy-match helpers | [agent-notes/normalization-and-matching.md](../agent-notes/normalization-and-matching.md) |
| YouTube | `src/utils/youtube/` — matching songs to YouTube videos, whether a video plays (yt-dlp + Data API), downloads, playlist management, keeping yt-dlp current (`ytdlp_update.py`) | [agent-notes/youtube-search-matching.md](../agent-notes/youtube-search-matching.md) |
| Terminal UI | `src/menu/`, `src/utils/ui/` — presentation, prompt flow, and wiring the other systems together | `AGENTS.md` → Module map |
| Config & settings | `src/config/constants.py`, `src/settings.py` — thresholds/labels, env-based paths and secrets | `AGENTS.md` → Module map, Secrets |

## Dependencies

One edge per line so both directions are `grep`-able.

- Terminal UI -> Catalog/DB search — resolves and displays `Song`/`Artist` rows for every menu flow
- Terminal UI -> Discovery — drives "Fill missing data", MP3 import, spell-check, and Statistics menus
- Terminal UI -> YouTube — drives playlist creation, video matching, and download menus
- Discovery -> Catalog/DB search — resolves existing rows and creates new `Artist`/`Song` rows during import; reads a song's artist credits (`song_artists.py`) for album/year lookups
- Discovery -> Terminal UI — `run_import_batch()` ends with the shared `review_artist_splits()` batch review (`utils/ui/artist_split_review.py`) and `offer_album_resolution()` (`utils/ui/album_resolution_review.py`)
- Discovery -> Normalization & matching — normalizes and scores fetched values against the query
- Discovery -> YouTube — `youtube_fetcher.get_release_year()` (Years) reuses `search_video_ytdlp()` and `get_video_release_year()` rather than a second YouTube matcher
- Catalog/DB search -> Normalization & matching — Python-side fallback filter, category/similarity matching
- YouTube -> Normalization & matching — scores candidate videos via `similarity()` / `scaled_similarity_threshold()`
- YouTube -> Catalog/DB search — `yt_cache.init_cache()` reads a song's artist credits (`song_artists.song_artist_credits()`) for matching, not for search
- Catalog/DB search -> YouTube — `inbox_requests.check_link_cases()` uses `search_video_ytdlp()`, `video_playability()` and `videos_exist_via_api()` to check reported dead links and links Leina Radio found
- Everything -> Config & settings — constants, thresholds, env-based paths/secrets

## Boundaries

- Terminal UI must not execute SQL directly — it goes through Catalog/DB search.
- YouTube never imports Discovery. Discovery imports YouTube only for that shared DB → YouTube
  matching (one implementation, see the edge above), and neither uses the other for DB search;
  Discovery is the one system besides Catalog/DB search that writes to the database, because
  import *is* a database-writing workflow.
- Full enforced rules: `AGENTS.md` → Architecture boundaries.
