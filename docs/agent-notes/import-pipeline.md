# Import pipeline — the MusicBrainz cost path

Scope: `import_data_from_mp3_tags.py` and the `check_spelling()` path it (and the
"Spell check existing data" menu, and `music_brainz_fetcher`) depends on. This
note is about *why importing MP3 tags is slow and what's been done about it* —
the mechanics of resolving artists/songs against the local DB are covered inline
in `AGENTS.md`.

## The bottleneck

Importing tags is almost entirely bottlenecked on the MusicBrainz web service,
not on any local work. A profile of a live import found the process spending
~100% of wall-clock time either in `time.sleep()` (rate-limit backoff) or blocked
reading the HTTP socket to `musicbrainz.org`; CPU use was effectively zero. Local
metadata extraction (mutagen) and the SQLite lookups are negligible by
comparison.

Each *new* artist and each *new* song triggers a `check_spelling()` call, and
each call can make up to two HTTP requests (a precise fielded query, then a broad
unfielded keyword query if the first returns nothing). MusicBrainz throttles
anonymous clients to roughly 1 request/second and answers 503 above that.

## Layered defenses (outermost first)

1. **DB-first shortcut** — `resolve_artist()` / `does_similar_song_exists()` only
   call `check_spelling()` when the local database has no good match. This is the
   oldest optimization and still the highest-value one: most files in a re-import
   never touch the network.
2. **Disk-backed cache** — `utils/common/spellcheck_cache.py`, persisted to
   `data/spellcheck_cache.json` (gitignored under `/data/`). Keyed by
   `(artist.strip().lower(), title.strip().lower())`. Stores **no-match results
   too** — those are the slowest to produce (they run both queries and eat any
   backoff), so not caching them would leave the worst case uncached. Atomic
   write (tmp + `os.replace`), autosave every 20 new entries, explicit
   `spellcheck_cache.save()` flush at the end of an import. Delete the file to
   force fresh lookups. This supersedes the two older in-memory dicts
   (`_SPELL_CHECK_CACHE` in the import module, `_MENU_SPELLCHECK_CACHE` in the
   spell-check menu) — they still exist and do no harm, but the disk cache is now
   the real one and it survives across runs and across the two call sites.
3. **Shared HTTP client** — `utils/common/musicbrainz_client.mb_get()`. One
   module-level `requests.Session` (so the `User-Agent` and connection pool are
   shared), a **process-wide** rate limiter (`threading.Lock` + last-request
   timestamp, `MUSICBRAINZ_API_MIN_INTERVAL` = 1.1s — just above 1/s to stay off
   the 503 threshold), and a hard `(connect, read)` timeout
   (`MUSICBRAINZ_API_TIMEOUT` = `(5, 12)`) so a stalled response can never hang an
   import forever (the old code passed no `timeout=` at all). All MusicBrainz
   traffic should route through this — a second call site doing its own
   `requests.get` would defeat the global rate limit.
4. **Split retry policy** in `mb_get()` — 429/503 get linear backoff
   (`5 * attempt` seconds) because they mean "cool off". Plain
   connection/timeout errors (`RequestException` that isn't an `HTTPError`) get
   *one* quick retry after 2s, because they usually mean a transient network or
   server hiccup, not that we're over quota — retrying those three times with
   escalating backoff was turning a single flaky lookup into a 60–70s stall.

## Why the fielded primary query gets only one attempt

`check_spelling()`'s `perform_search()` runs the precise query
(`recording:<fuzzy title> AND artist:<fuzzy artist>`) with `retries=1`, then
falls through to the broad keyword query (`<artist> <title>`) with the normal
`retries=2`. Observed behaviour under load (and when another import is already
consuming this IP's MusicBrainz allowance): the fielded query is the first thing
MusicBrainz drops — it times out or resets — while the cheap keyword query still
succeeds. Burning two full read-timeouts on the fielded query before even trying
the fallback was the single biggest contributor to multi-second per-file times.
Giving it one shot and moving on roughly halves the worst case.

The fallback query itself is toggleable via
`MUSICBRAINZ_SPELLCHECK_USE_FALLBACK` (default `True`). It roughly doubles the
request count for every miss; turn it off to trade recall for speed on large
imports.

## Observability

`MBStats` (in `musicbrainz_client.py`) is a plain class of counters:
`requests_made`, `cache_hits`, `cache_misses`, `http_429`, `http_503`,
`other_errors`, `total_wait_seconds`, `total_request_seconds`.
`import_data_from_mp3_tags()` calls `MBStats.reset()` at the start and prints
`MBStats.format_summary()` in the run summary. If an import is slow, that summary
tells you immediately whether it's cache misses (genuinely new data), 503s (rate
limit — is another client running?), or `other_errors` + high
`total_request_seconds` (MusicBrainz dropping connections).

## User-Agent

MusicBrainz wants a real application/version/contact. `constants.py` sets
`MUSICBRAINZ_API_USER_AGENT = "MusicDatabase/1.0 ( https://github.com/shian1000/MusicDatabase )"`
and `music_brainz_fetcher.py` mirrors it (both its `HEADERS` and its
`musicbrainzngs.set_useragent(...)` call). The contact is the project repo URL,
not a personal email — keep it that way if you touch it.
