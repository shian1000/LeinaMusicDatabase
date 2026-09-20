# System Registry

One row per subsystem. "Docs" links to where the *depth* lives — a system without a dedicated
`agent-notes` file yet is documented inline in `AGENTS.md`'s Module map.

| System | Owns | Docs |
| --- | --- | --- |
| Catalog / DB search | `src/utils/database/` — storage for artists/songs/tags, the search/getter API, the category system | [agent-notes/database-search.md](../agent-notes/database-search.md) |
| Discovery | `src/utils/discoveries/` — external metadata fetchers (MusicBrainz, Wikipedia, iTunes, Genius, Google, Spotify, YouTube), MP3-tag import | [agent-notes/discovery-modules.md](../agent-notes/discovery-modules.md), [agent-notes/import-pipeline.md](../agent-notes/import-pipeline.md) |
| Normalization & matching | `src/utils/common/normalizer.py`, `text_utils.py` — canonical string normalization and fuzzy-match helpers | [agent-notes/normalization-and-matching.md](../agent-notes/normalization-and-matching.md) |
| YouTube | `src/utils/youtube/` — matching songs to YouTube videos, downloads, playlist management | [agent-notes/youtube-search-matching.md](../agent-notes/youtube-search-matching.md) |
| Terminal UI | `src/menu/`, `src/utils/ui/` — presentation, prompt flow, and wiring the other systems together | `AGENTS.md` → Module map |
| Config & settings | `src/config/constants.py`, `src/settings.py` — thresholds/labels, env-based paths and secrets | `AGENTS.md` → Module map, Secrets |

## Dependencies

One edge per line so both directions are `grep`-able.

- Terminal UI -> Catalog/DB search — resolves and displays `Song`/`Artist` rows for every menu flow
- Terminal UI -> Discovery — drives "Fill missing data", MP3 import, spell-check, and Statistics menus
- Terminal UI -> YouTube — drives playlist creation, video matching, and download menus
- Discovery -> Catalog/DB search — resolves existing rows and creates new `Artist`/`Song` rows during import
- Discovery -> Normalization & matching — normalizes and scores fetched values against the query
- Catalog/DB search -> Normalization & matching — Python-side fallback filter, category/similarity matching
- YouTube -> Normalization & matching — scores candidate videos via `similarity()` / `scaled_similarity_threshold()`
- Everything -> Config & settings — constants, thresholds, env-based paths/secrets

## Boundaries

- Terminal UI must not execute SQL directly — it goes through Catalog/DB search.
- YouTube and Discovery don't import each other or the database layer for search purposes;
  Discovery is the one system besides Catalog/DB search that writes to the database, because
  import *is* a database-writing workflow.
- Full enforced rules: `AGENTS.md` → Architecture boundaries.
