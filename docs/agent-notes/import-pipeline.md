# Import pipeline — the MusicBrainz cost path

Scope: `utils/discoveries/import_engine.py` (the shared artist/song resolution +
batch-conflict-review engine) and the `check_spelling()` path it (and the "Spell
check existing data" menu, and `music_brainz_fetcher`) depends on. This note is
about *why resolving artists/songs against MusicBrainz is slow and what's been
done about it*. The mechanics of resolving artists/songs against the local DB are
in `docs/agent-notes/database-search.md`; string normalization and the
fuzzy-match helpers are in `docs/agent-notes/normalization-and-matching.md`.

## Two source-specific builders, one shared engine

`resolve_artist()`, `resolve_song()`, `create_song_entry()`,
`find_similar_artist()`, `find_similar_song()` and the batch-conflict-review
loop all live in `import_engine.py`, behind one entry point:
`run_import_batch(metadata_list, pre_skipped=None)`. It used to be that
`import_data_from_mp3_tags.py` owned all of this directly; it was split out so
`utils/youtube/import_from_playlist.py` ("Import data from YouTube playlist")
could reuse the exact same matching/dedup/conflict-review machinery instead of
forking it. If you're chasing `resolve_artist`/`resolve_song`/pending-conflict
logic and it's not in `import_data_from_mp3_tags.py` anymore, it's in
`import_engine.py` now.

Each source is now just a **metadata builder**: it turns its own input (mp3
files on disk, or a YouTube playlist's videos - see
`docs/agent-notes/youtube-search-matching.md` for that side) into a list of
plain dicts (`artist_name`, `title` required; `album`, `year`, `language`,
`origin`, `youtube_video_id`, `_label` optional - `_label` is what shows up in
skip/timing messages instead of the default `"<artist> - <title>"`), then hands
that list to `run_import_batch()`. `run_import_batch()` itself doesn't know or
care which source it came from - a metadata dict missing `artist_name`/`title`
entirely is just skipped with reason `"Missing artist or title"`, which is how
an mp3 with no usable filename fallback and a YouTube title that failed to
split both end up handled by the same generic path rather than two different
skip mechanisms.

**`Song.youtube_video_id` backfill:** `create_song_entry()` sets
`Song.youtube_video_id` from `metadata.get("youtube_video_id")` whenever it's
present (a no-op for mp3-sourced metadata, which never sets that key). More
importantly, when an import matches an *existing* song instead of creating a
new one, `_backfill_youtube_link()` writes that video id onto the existing
`Song` row if it doesn't already have one - both at the immediate
exact-title-match shortcut in `resolve_song()` and in the end-of-batch
"do you want to use the existing song?" review when the user says yes. This
means a YouTube playlist import can silently mutate rows it doesn't "add" at
all; if you're debugging why an old song suddenly has a `youtube_video_id` it
never had before, this is why.

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

1. **DB-first shortcut** — `resolve_artist()` / `find_similar_song()` only call
   `check_spelling()` when the local database has no good match. This is the
   oldest optimization and still the highest-value one: most files in a re-import
   never touch the network. `resolve_artist()`'s local-match half (exact, then fuzzy
   against length-similar candidates) is factored out as `find_matching_artist()` /
   `_match_artist_locally()` - the latter also returns whether *any* local
   candidate existed at all (not just whether one passed the similarity bar),
   which is what `resolve_artist()` uses to decide whether a MusicBrainz
   fallback is even worth attempting. `find_matching_artist()` is reused outside
   this module too, by the YouTube importer's artist/title swap detection - see
   `docs/agent-notes/youtube-search-matching.md`.
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

## `check_spelling()` return shape

`check_spelling(artist, title)` always returns a dict with `corrected_artist` /
`corrected_title` **present**: on a MusicBrainz hit they hold the corrected
values (plus `_norm` variants, scores, `"found": True`); on no match they echo
the unchanged inputs with `"found": False`.

Callers still branch on `spell_check_result.get("found")` for **semantics** — an
unchanged echo is not a real correction. `find_similar_artist()` returns `[]` on
the not-found path; `find_similar_song()` returns `None` (same guard,
caller-appropriate sentinel per return type).

Historically the no-match path returned a different stub shape *without* the
`corrected_*` keys, which repeatedly caused `KeyError` crashes (the
artist-matching lookup on `corrected_artist`, the song-matching lookup on
`corrected_title`, the spell-check menu on both). The shape was unified when the
disk-backed cache landed — but keep new call sites shape-agnostic anyway
(`.get()` with a guard on `found`), since the disk cache stores the no-match
answers too and a cache hit reproduces whatever shape was stored.

## Ambiguous artist/song matches are asked about after the batch, not inline

Neither `find_similar_artist()` (formerly `check_artist_spelling()`, now
returns a `list[Artist]` instead of asking per-candidate) nor `find_similar_song()`
(formerly `does_similar_song_exists()`) prompts the user — both are pure lookups.
All the "do you want to use the one already in the database?" questions are
asked in two batches at the very end of `run_import_batch()` (see above -
shared by every import source), after every entry in `metadata_list` has been
processed, instead of interrupting the import per entry. This runs before the
function returns — i.e. before the caller's own "Do you want to do something
with these songs?" prompt in
`enter_database/manage_database/fetch_database_data/__init__.py`.

**Artist conflicts (asked first):** `resolve_artist()` takes a
`pending_conflicts` list. When its local-candidate search and its
`find_similar_artist()` fallback both miss but the corrected spelling turns up
one or more DB matches, it appends
`{"metadata", "normalized_name", "candidate_artists"}` to that list, stores the
sentinel `_PENDING_ARTIST` in `artist_cache[normalized_name]`, and returns
`(None, is_pending=True)`. The main loop in `run_import_batch()` records the
entry in `deferred_entries` and `continue`s — no song resolution happens for it
yet. Because the pending state is cached, a second entry with the *same* artist
name hits the cache-hit branch in `resolve_artist()` and also returns pending
immediately, without re-querying MusicBrainz or queueing a duplicate conflict —
one artist name produces exactly one question, however many of its songs are in
the batch.

After the main loop, `run_import_batch()` walks `pending_artist_conflicts`
once, asking about each `candidate_artists` entry in order (first confirmed
wins; none confirmed creates a new artist) and writes the resolved `Artist`
back into `artist_cache[normalized_name]`. It then replays `deferred_entries`
through `resolve_artist()` (now an instant cache hit) and `resolve_song()` —
which may itself queue a similar-song conflict, same as any other entry.

**Song conflicts (asked second, so they include ones surfaced by the artist
replay above):** `resolve_song()` takes its own `pending_conflicts` list; on a
`find_similar_song()` hit it appends `{"metadata", "artist_obj",
"existing_song"}` and returns `(None, is_pending=True)` instead of asking `Do
you wish to use the song already in the database?` right there. Resolved the
same way — one final pass over the queue, updating `added_count`/`skipped_count`
as each is answered.

The point throughout is purely UX: a batch with several ambiguous artists or
near-duplicate songs used to stop the import for input over and over; now it
runs to completion unattended and both kinds of review happen as two batches at
the end, artists before songs (since a song's dedup check needs its artist
resolved first).

## The "Spell check existing data" menu batches the same way

`check_spelling_menu()` (`manage_database/__init__.py`) runs the same
`check_spelling()` calls over every song already in the DB (not just an
import), auto-applies a correction when `should_auto_confirm()` matches one of
`AUTO_CONFIRM_RULES`, and otherwise used to ask `Do you wish to correct this
artist name?` / `...song title?` inline, per song. It now queues those instead
of asking, and reviews them in one batch after every song has been checked -
same reasoning and same shape as the import path above.

Two queues, not one, because of a data-model difference from the import path:
`pending_title_corrections` is a plain list (`{"song", "old_title",
"new_title"}`, one entry per song - titles are never shared), but
`pending_artist_corrections` is a **dict keyed by `song.artist.id`**. `song.artist`
is a shared SQLAlchemy object: in the original per-song-inline code, confirming
one song's artist rename mutated `song.artist.name` immediately, so the *next*
song by that artist would already see `new_artist == song.artist.name` and
silently skip re-asking. Deferring the mutation to the end loses that
side-effect-driven dedup for free, so the dict does it explicitly - only the
first song to hit a given `artist.id` queues the question; every later song by
the same artist finds it already queued and does nothing. If two songs by the
same artist somehow produce two different corrected spellings, the first one
queued wins and the second is silently dropped rather than asked (matches how
`resolve_artist()`'s `pending_conflicts` in the import path takes "first
candidate confirmed" and ignores the rest).

Tagging a song `spellchecked` and `submit_global_database_session()` still
happen immediately per song, independent of whether its correction is later
confirmed - that tag means "already asked MusicBrainz about this song", not
"correction applied", so it must not wait on the deferred review or the next
menu run would burn another API call re-checking it.

## Observability

`MBStats` (in `musicbrainz_client.py`) is a plain class of counters:
`requests_made`, `cache_hits`, `cache_misses`, `http_429`, `http_503`,
`other_errors`, `total_wait_seconds`, `total_request_seconds`.
`run_import_batch()` calls `MBStats.reset()` at the start and prints
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
