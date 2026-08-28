# Discovery module fetchers (`src/utils/discoveries/`)

`discoveries_manager.py` dynamically loads the *enabled* files in `discovery_modules/` and tries
each one in turn via `get_album_name(artist, title)`, in the user-configured order, until one
returns an album for the song being looked up.

Fetch priority used to be pinned by numeric filename prefixes (`1music_brainz_fetcher.py`, etc.);
that's gone. Order and enabled/disabled state now live in `discovery_settings.py`, persisted to
`discovery_modules_config.json` (gitignored, repo root) keyed by filename stem, and are editable
at runtime via Settings -> Discovery modules in the main menu
(`src/menu/main_menu/settings/__init__.py`). See `AGENTS.md` for the mechanics.

## The Settings menu reads `MODULE_NAME` without importing the module

Two loaders in `discoveries_manager.py`, deliberately asymmetric:

- `load_discovery_modules()` — the actual "Fill missing data -> Albums" path. Skips disabled
  modules *before* importing them, so a disabled module's import-time code never runs.
- `load_all_discovery_modules_metadata()` — the Settings enable/disable + reorder screens. Needs
  every module's display name (enabled or not), but does **not** import them. `_read_module_name()`
  statically parses each file with `ast` and pulls the `MODULE_NAME` value out of the syntax tree.

The reason it doesn't just import them: the fetchers do real work at import time — `selenium`,
`requests`, `musicbrainzngs.set_useragent(...)`, `import wikipedia` — and importing all five
(including disabled ones) just to read one string each is pure waste every time someone opens the
Settings menu.

Consequence for anyone adding or renaming a fetcher: `MODULE_NAME` **must be a plain top-level
string-literal assignment** (`MODULE_NAME = "Foo fetcher"`). An f-string, a concatenation, or a
computed value is invisible to the static parser — the Settings menu will silently fall back to
showing the filename stem (the runtime path still works, since `load_discovery_modules()` reads it
via `getattr` on the real module). Keep it a literal.

## Title/artist truncation before querying, and the untruncated fallback

Before calling any module, `discover_album_name()` runs the artist and title through
`truncate_at_word()` (`text_utils.py`), which cuts the string off at the first occurrence of a
stop word (`feat`, `&`, `ft.`, `, `, etc.) — meant to strip trailing collaborator credits like
"Song Title feat. Other Artist" down to just "Song Title" before searching, since most fetchers
match better without them.

The catch: the bare `", "` stop word matches *any* mid-string comma-space, not just a trailing
credits list, so it also truncates legitimate titles/artists that happen to contain one — e.g.
"Bang, Bang" becomes just "Bang" before it ever reaches a fetcher's query. Rather than special-case
the stop-word list (which is inherently ambiguous — a comma could be a real credits separator or
part of the title), `discover_album_name()` compensates with a fallback: if a module's truncated
query comes up empty *and* truncation actually changed something, it retries that same module once
more with the original, untruncated (but still parenthetical-stripped) artist/title before moving
on. This runs before the synonym-retry step. Because it's implemented in the manager rather than
per-module, every fetcher gets the fallback for free.

## Why the manager validates results itself instead of trusting modules

Each fetcher scrapes/searches its own source and, historically, did its own match verification
(comparing a candidate result's title/artist to the query before accepting it) — the manager just
took whatever a module returned at face value.

That trust broke in practice: `genius_fetcher.py` had a loop that correctly searched Genius
results for one whose URL slug matched the query title, but a leftover line right after the loop
(`song_url = link.get_attribute("href")`) unconditionally overwrote the verified match with
whichever search result the loop happened to end on — silently discarding its own verification and
scraping an unrelated song's page whenever no real match was found. That's common for exactly the
songs likely to have a missing album in the first place: niche tracks like game soundtracks,
covers, and live versions that Genius doesn't index. The observed symptom was a real user's
"Fill missing data -> Albums" run confidently writing one song's actual album into a *different*,
unrelated song's DB record. (It was not an indexing bug in `fill_missing_albums.py` — that pairing
logic was verified clean via an isolated repro against a copy of the real DB before looking
elsewhere.)

The point bug is fixed, but a fetcher module's own verification logic having a bug like this isn't
something the manager can prevent from happening again in a *different* module by fixing this one.
So verification was moved to be defense-in-depth at the manager level, independent of whether a
given module's internal logic is correct.

## The contract: `DiscoveryResult`

`discovery_result.py` defines:

```python
@dataclass
class DiscoveryResult:
    album: str
    matched_title: Optional[str] = None
    matched_artist: Optional[str] = None
```

A fetcher's `get_album_name()` can return this instead of a bare `str`. `matched_title` /
`matched_artist` must be whatever the module actually found on the page/response it scraped the
album from — never an echo of the input query, since echoing the input would always "match" and
defeats the whole point. Leave a field `None` if the module has no reliable way to extract it.

`discoveries_manager._validate_result()` then, for every module call (upgraded or not):
- Wraps it in try/except — a crashing module is logged and skipped, not fatal to the whole run.
  (Before this, fetcher exceptions propagated uncaught all the way to the top-level crash handler;
  they accounted for a large share of `debug.log`'s crash entries.)
- Rejects non-string, empty, or blacklisted albums.
- If `matched_title`/`matched_artist` were provided, independently checks their similarity against
  the *query* — not the module's own opinion of whether it matched — using
  `scaled_similarity_threshold()` (the same short-string-aware threshold used elsewhere in the
  app), and discards the result if it doesn't resemble what was actually searched for.

Plain `str` returns still work (they just skip the similarity cross-check), so an un-upgraded
module degrades gracefully rather than breaking.

## Which fetchers report matches

- `music_brainz_fetcher.py` — reports the title/artist of the MusicBrainz *recording* that the
  chosen release came from.
- `itunes_fetcher.py` — reports the JSON-LD `audio.name` / `byArtist[0].name` it already scrapes
  to verify the match internally.
- `genius_fetcher.py` — reports the song-title portion of the matched URL slug (artist prefix
  stripped best-effort by `_title_portion_of_slug()`, since Genius slugs are `artist-title-lyrics`
  with no clean delimiter).
- `google_search_fetcher.py` — reports the artist field from the Google Knowledge Panel when
  present; no reliably-extractable matched title from that source.
- `wikipedia_fetcher.py` was **not** upgraded — it already requires the query title to appear in
  the page/container text before accepting a result, so the marginal benefit was low, and its
  extraction logic wasn't touched.

## Don't remove the per-candidate blacklist checks

`music_brainz_fetcher.py` and `wikipedia_fetcher.py` both call `is_blacklisted_album()` *inside*
a loop over many candidate releases/headings, to filter out bad options while picking the best one
(batch filtering). That is a different job from the manager's blacklist check on the *final*
returned album (a last-resort net). Don't treat the per-candidate calls as redundant with the
manager's check and remove them — they serve a genuinely different purpose and are still needed.
