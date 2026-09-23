# String normalization & fuzzy matching (`src/utils/common/`)

Scope: `normalizer.py` (canonicalising messy strings), `split_artist_title()` /
`extract_unknown_data()` in the same file (splitting "Artist - Title" text -
from a filename stem, or any other string), and the similarity helpers in
`text_utils.py` (comparing the canonicalised strings).

## One normalizer, extended — never a second one

`normalizer.py` is the **single, centralized** string-normalization
implementation: `normalize()` and `compare()`, handling diacritics, Unicode
scripts, apostrophes, and punctuation. `text_utils.normalize_text()` and other
call sites delegate to it.

Do not write a new ad-hoc normalize/compare function anywhere else — extend this
one.

## `split_artist_title(name)` — three ordered fallback stages

The actual "Artist - Title" splitter — three stages, each tried only if the
previous one found no separator. **Keep them in this order and don't merge them
into one greedier regex** — each is a fallback for when the stricter,
less-error-prone stage before it fails. `extract_unknown_data(filepath)` is a
thin wrapper around this (`split_artist_title(filepath.stem)`) for deriving
artist/title from an mp3 filename when ID3 tags are missing or empty; it was
factored apart so `utils/youtube/import_from_playlist.py` could reuse the same
splitting logic directly on a YouTube video title (not backed by a file at all)
— see `docs/agent-notes/youtube-search-matching.md`'s "Importing FROM a YouTube
playlist" section.

1. **Spaced separator required** — ` - `, ` – ` (en dash, U+2013), ` — ` (em
   dash, U+2014), or ` _ `. En dash and em dash are visually near-identical but
   distinct codepoints, and real-world filenames use both (e.g. `Zdob și Zdub —
   La mijloc...`).
2. **Bare `-` / `_` / `~`, no surrounding spaces required** — recovers
   `Emade-Pierwszy stopień wtajemniczenia.mp3`, `Die Ärzte _Unrockbar_.mp3`,
   `Haiku Garden ~ Avalanš.mp3`; strips stray `-` / `_` / `~` / spaces off both
   halves. Intentionally permissive and a genuine last resort: a title with an
   incidental hyphen and no real artist (`Re-Animator.mp3`) misparses into
   artist `Re` / title `Animator` here rather than being reported unparseable —
   acceptable only because stage 1 already failed.
3. **Split on 2+ consecutive spaces** — recovers filenames with no punctuation
   separator at all, e.g. `Akiko Yano  Iroha Ni Konpeitou.mp3`, where the
   artist/title boundary is just a double space.

## `text_utils` similarity helpers — three functions, not one with three signatures

- `similarity(a, b)` — the plain two-string comparator, 0–1 ratio backed by
  `difflib.SequenceMatcher`. Use this when comparing two raw strings.
- `are_song_entries_similar(db_object, title_query, artist_query, threshold)` and
  `are_artists_entries_similar(db_object, artist_query, threshold)` — these
  require an actual **DB object** as the first arg (they read `.title` /
  `.artist.name` / `.name`), not a second plain string. Passing two plain strings
  raises `TypeError` — this broke `discovery_modules/genius_fetcher.py`'s
  `title_matches_url()` until fixed.

Comparing two strings → `similarity()`. Only reach for `are_*_entries_similar`
when you actually have a DB row to compare a query against.

## `SequenceMatcher` ratio is unreliable on short strings

Two different names that share a first letter and a common suffix can still score
high just because most characters line up in a short string — e.g.
`similarity("A.Mia", "Armia")` is `0.8`.

When comparing a similarity score against a match threshold **for artist names**,
use `scaled_similarity_threshold(a, b, base_threshold)` rather than comparing
against `base_threshold` directly. It raises the required ratio for short strings
(≤5 chars → 0.92, ≤8 chars → 0.85) before falling back to `base_threshold` for
longer names.

`are_artists_entries_similar()` applies this internally.
`import_data_from_mp3_tags.resolve_artist()` and `check_artist_spelling()` call
it explicitly at their fuzzy-match checks.
`discoveries_manager._validate_result()` uses it for its title/artist cross-check
too.

## A shared bracketed suffix inflates `similarity()` on otherwise-different titles

Two genuinely different song titles that happen to share a `(prod. X)` /
`(feat. Y)`-style bracketed credit can score above the usual 0.7 threshold on
the bracket text alone — e.g. `similarity("Adieu (prod. Rumak)", "Nostalgia
(prod. Rumak)")` is `0.76`, because roughly 15 of the ~20-24 characters being
compared are the identical `(prod. Rumak)` suffix.

`import_engine.find_similar_song()`'s local-DB duplicate check strips bracketed
content with `remove_brackets()` from both titles before calling `similarity()`
for exactly this reason. Any other duplicate-song check comparing raw titles
should do the same rather than comparing `song.title` strings directly.
