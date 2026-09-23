# YouTube search matching & playlist import (`src/utils/youtube/`)

Two directions live under `src/utils/youtube/`, covered in the two halves of this file:

- **DB → YouTube** (`manage_youtube_playlists.py`): given a song already in the database, find the
  matching YouTube video to build a playlist. `score_result()` picks the "best match" video,
  searching via yt-dlp first (free) then falling back to the YouTube Data API. Several earlier,
  simpler versions of this function caused real wrong-song matches — most of this file documents
  why the current logic looks the way it does, so it doesn't regress if simplified later.
- **YouTube → DB** (`import_from_playlist.py`, see "Importing FROM a YouTube playlist" below): given
  a playlist URL, pull its videos into the database as new songs — the "Import data from YouTube
  playlist" menu item, sibling to the mp3-tag importer (see `docs/agent-notes/import-pipeline.md`).

## Importing FROM a YouTube playlist (`import_from_playlist.py`)

**⚠ Debug scaffolding currently left active:** `import_data_from_youtube_playlist()` has a line
`items = items[:1]` right after fetching the playlist, added mid-session so the user could test
one song at a time without waiting through the full matching pipeline for an entire playlist.
Right now the menu option **only ever imports the first video of any playlist**. Remove that line
(or turn it into a real, deliberate limit setting) before treating full-playlist import as working.

### Fetching: yt-dlp first, Data API (OAuth) fallback

`get_playlist_items()` tries yt-dlp first (no auth, no quota - works for anything reachable
anonymously, public or unlisted) and only falls back to `get_youtube_service()` +
`playlistItems().list()` (OAuth, as the signed-in account) when yt-dlp returns `None` - which
happens on a missing binary, a timeout, or literally empty stdout, the same shape a genuinely
Private playlist produces. `--ignore-errors` means one bad/deleted video in the middle of an
otherwise-fetchable playlist doesn't trigger the fallback, just gets skipped.

**Must use `--flat-playlist`.** Plain `--dump-json` (no flag) fetches full metadata for *every*
video individually - measured at ~1.8s/video on a real playlist. A real 441-video playlist
projects to ~13 minutes that way, blowing past the subprocess's 180s timeout and looking exactly
like a hang (this happened during testing - the user reported "nothing happens" after pasting a
441-song playlist). `--flat-playlist` reads the playlist page itself only and returns the whole
441-video list in ~4s, while still exposing every field `build_metadata_from_item()` needs
(`id`, `title`, `channel`/`uploader` - verified directly against a real playlist, these are
populated the same as the non-flat form). If a future change needs a field `--flat-playlist`
doesn't provide, don't just drop the flag - it will silently reintroduce this timeout.

### Turning one playlist video into artist/title

`build_metadata_from_item()`:
1. If the channel is a `"<Artist> - Topic"` auto-generated channel (`_is_topic_channel()`, reused
   from `manage_youtube_playlists.py`), the artist is the channel name minus the `"- Topic"` suffix
   and the title is the video title as-is - same reasoning as the DB→YouTube direction's Topic
   handling (see below): these titles deliberately carry no artist text at all.
2. Otherwise, try `split_artist_title()` (`normalizer.py` - the same "Artist - Title" splitter
   `extract_unknown_data()` uses for mp3 filenames, factored out so both callers share it) on the
   video title.
3. If that finds no separator at all, fall back to the channel name as the artist anyway - real
   case found testing this: Culture Beat's own (non-Topic) channel, video titled just
   `"Mr. Vain (Original Radio Edit)"` with no artist text in the title whatsoever. This is
   apparently common for plain artist-channel uploads, not just auto-generated Topic channels.

### Title cleanup: three independently-extensible word lists, applied in a fixed order

`build_metadata_from_item()` first runs `strip_pipe_suffix()` -> `strip_junk_brackets_anywhere()`
-> `strip_hashtags()` over the *raw* video title (before any artist/title split), then later runs
`strip_artist_from_title()` -> `strip_junk_suffix()` -> `strip_quote_marks()` on the resulting song
title. The order of the first three matters: `strip_pipe_suffix()` runs before the bracket check
specifically so a junk bracket that only *looks* trailing once channel/label branding after a `|`
is dropped (e.g. `"OMNIMAR - The Matrix (Official Video) | darkTunes Music Group"`) becomes
reachable by the trailing-only check below, instead of needing its own "anywhere" rule.

Bracket-content matching is split by **scope**, not just by exact-vs-substring:

- `YOUTUBE_TITLE_JUNK_PHRASES` (checked by `strip_junk_suffix()`) - the **trailing** bracket's
  entire content must match one of these exactly (case-insensitive) - e.g. `"Original Radio Edit"`,
  `"Official Video"`, `"HD"`, `"Edit"` (exact `"Edit"` only - `"Club Edit"`/`"Radio Edit"` are
  deliberately real alternate versions and must never be caught as a marker word instead).
- `YOUTUBE_TITLE_JUNK_MARKER_WORDS` (checked by `strip_junk_suffix()`, same trailing-only scope) -
  a single word appearing anywhere *inside* the trailing bracket condemns the whole thing, for
  content too source-specific to enumerate as exact phrases - currently `"Soundtrack"` (real case:
  `"(Pes 2009 Soundtrack)"`) and `"Audio"`.
- `YOUTUBE_TITLE_JUNK_MARKER_WORDS_ANYWHERE` (checked by `strip_junk_brackets_anywhere()`, run over
  the *whole raw title*, not just its end) - for junk brackets that sit mid-title with real title
  text after them, which the trailing-only checks above can never reach - e.g.
  `"Herr Mannelig (animation) - Żniwa - Polish version"` or
  `"Voo Voo - Nocą (Official Audio) #Zostańwdomu"`. Currently `"Animation"`, `"Official"`,
  `"Lyrics"`. Only add a word here when it's safe to assume it never appears inside a genuine
  subtitle, since - unlike the two lists above - it isn't limited to the trailing bracket, so a
  false-positive match anywhere in the title is destructive.

`_TRAILING_BRACKET_RE` and `_ANY_BRACKET_RE` both also treat the CJK "lenticular bracket" pair
`【】` as equivalent to `()`/`[]` (e.g. a trailing `"【Kan/Rom/Eng Lyrics】"` annotation). Separately,
`strip_quote_marks()` strips double-quote-*like* characters from the song title - ASCII/curly
double quotes and the CJK "corner bracket" pair `『』` (used to set off a work's title the way
Western uploads wrap it in `"..."`, e.g. `'『Cinderella by Cidergirl』'`) - but deliberately never
single quotes/apostrophes, since those routinely appear inside a genuine title (`"Rock 'n' Roll"`,
`"Don't Stop"`). `strip_hashtags()` drops every `#word` promo-hashtag token, wherever it appears.

All three word lists are user-curated and meant to grow by appending new entries as new junk shapes
turn up - deliberately excluded so far: anything that marks a real alternate version worth keeping
distinguishable (`Remix`, `Live`, `Acoustic`, `Cover`, `Extended`, `Club Edit`, a named remixer, a
`feat./ft.` credit). A genuine subtitle earlier in the title (e.g. `"Sad Story (Out of Luck)"`,
Merk & Kremont) is left alone by the trailing-only lists precisely because they never look past the
end of the string - only add a word to the `_ANYWHERE` list once you're sure it can't collide with
that kind of real subtitle anywhere in a title.

`strip_artist_from_title()` runs before the bracket cleanup, for when the video title repeats the
artist name outside any bracket (real case: channel `"SIAMES"`, video titled
`'SIAMÉS "Summer Nights" [Official Animated Video]'`). Diacritic-insensitive on both sides via
`_fold_char()` (NFKD-fold one character at a time, so the folded string stays the same length and
position-maps 1:1 back onto the original for slicing - a from-scratch position-preserving variant
of the whole-string `_fold_diacritics()` already used in `manage_youtube_playlists.py`, needed here
because *removing* a match requires knowing where it was in the un-folded original). Also unwraps a
now-orphaned quote pair left behind when the artist name was itself quoted (the SIAMES case above),
and refuses to empty the title out entirely (e.g. a self-titled artist == title stays untouched).

### Album name: investigated, deliberately not implemented

YouTube's video-description "Music" info panel (visible in the UI, and in the overflow-menu dialog
literally labelled `Utwór`/`Wykonawca`/`Album`) *is* real structured data - confirmed by fetching a
raw watch page and finding `videoAttributeViewModel` (`title`/`subtitle`/`secondarySubtitle`) inside
a `horizontalCardListRenderer` under `structuredDescriptionContentRenderer` in the page's
`ytInitialData` - not free-text description scraping. But: yt-dlp doesn't expose it at all (absent
from `--dump-json`/`--flat-playlist` output entirely - confirmed by walking every key of a real
video's dump), it's only present when the uploader/label registered the track (no guaranteed
coverage), the schema is undocumented/internal (no stability guarantee), and reaching it needs
fetching the full ~1.2MB watch page HTML **per video**, which would reintroduce the exact timeout
problem `--flat-playlist` above was added to fix if done for every video in a playlist listing.
Conclusion reached with the user: worth doing later as a one-video-at-a-time "enrich this song"
step (only for songs actually being added, not the whole playlist scan), not as part of the
playlist-listing pass. Not implemented.

## Why title-only, not artist+title combined

Comparing the full "artist title" string against a candidate's title let two failure modes slip
through:
- A wrong song by a same-named (or similarly spelled) artist could score deceptively high just
  from the artist portion matching.
- A wrong candidate that merely shared a caption/tag with the DB title (e.g. two unrelated videos
  both carrying `[Save Ukraine - #StopWar]`) could look similar for the wrong reason.

`score_result()` computes relevance from the song title alone. Bracket/hashtag stripping
(`_relevance_text()`) removes annotation noise like `[Official Video]` or `#hashtags` from both
sides before comparing, for the same reason.

## Why relevance isn't just similarity()

Plain `difflib.SequenceMatcher` ratio (`text_utils.similarity()`) is unreliable at both ends for
song titles:
- A short, exact title buried in a longer, decorated candidate title gets diluted (title "As" vs
  candidate "Gosia Kunc - As" scores only ~0.24 despite being an exact match — the "Gosia Kunc - "
  prefix dilutes the ratio).
- A short title can *also* score deceptively high against something totally unrelated by sheer
  character-overlap coincidence (`similarity("nix", "netflix top 10 trailer compilation")` ≈ 0.67).

Two mechanisms fix this, together:
- `_title_containment()` checks whether the expected title shows up intact inside the candidate.
  For space-delimited scripts this requires a whole-word/phrase regex match (`\btitle\b`) — not a
  raw substring — otherwise "As" would falsely "contain-match" inside "Asian Kungfu Generation...".
  Scripts written without spaces (CJK ideographs, hiragana/katakana, hangul — matched by
  `_NO_SPACES_SCRIPT`) have no word boundary to anchor on, so a contiguous longest-common-run
  fraction is used instead; that's safe there because each character carries far more information
  than a Latin one, so short runs aren't the same coincidental-match risk.
- `_min_relevance_for()` scales the acceptance bar up for short titles, reusing the
  `scaled_similarity_threshold()` helper from `text_utils.py` (already used there for short artist
  names) instead of a flat constant. This is what actually closes the "netflix ≈ nix" coincidence —
  containment alone isn't picky enough at very short lengths, so the threshold has to be.

Relevance is `max(similarity(...), containment(...))`, gated by `_min_relevance_for(title)` — a
per-title dynamic value, not the flat `MIN_RELEVANCE` constant (that constant is only the floor,
used as-is for long/distinctive titles).

## Why channel/uploader is part of scoring

YouTube auto-generates a `"<Artist> - Topic"` channel per artist for official, single-track audio
uploads — the video *title* on these is deliberately just the bare song title, with no artist
mentioned anywhere in it. `score_result(candidate_title, artist, title, channel="")` handles this
two ways:
- Artist relevance (a soft tiebreaker, not a gate) also checks the channel name, not just the
  title — otherwise a correct Topic-channel match would look like it has zero artist relevance.
- `_is_topic_channel()` gives a `+2` quality bonus to Topic-channel uploads (same weight as an
  "official audio" keyword hit), since they're exactly the plain, unedited version a personal
  library wants.

Both search paths must keep passing `channel` through: `search_video_ytdlp` gets it from yt-dlp's
`channel`/`uploader` JSON fields, `search_video` from the API's `snippet.channelTitle`. Dropping it
silently loses both effects.

## YouTube search backend can silently return zero results for a query, not just a bad match

`score_result()`/`_min_relevance_for()` (above) only matter once at least one candidate comes
back — they can't rescue a search that returns nothing. That case is real: YouTube's own search
backend returns literally `0 items` for short queries matching a `"<name> Sex ..."` pattern (a
bare trigger word next to a short proper noun with no softening context), treating it as adult
search intent even when the actual video is ordinary music. Verified this isn't specific to one
artist — `"Berlin Sex audio"`, `"Madonna Sex audio"`, `"Paris Sex audio"`, and `"London Sex
audio"` all return zero, while longer, more sentence-like titles that merely contain "sex" (e.g.
`"Let's Talk About Sex"`, `"I Wanna Sex You Up"`) are unaffected. This hits `search_video_ytdlp()`
(anonymous scraping, no way to disable) and hit `search_video()`'s YouTube Data API fallback too,
because the API's `safeSearch` param defaults to `"moderate"` when unset. Both `search_video()`
and `search_video_cached()` now pass `safeSearch="none"` explicitly to opt out of that filtering —
the yt-dlp path still comes back empty for these queries, but the API fallback (already the
designed catch-all) now succeeds instead of also returning nothing.

Regression test: [tests/test_youtube_search.py](../../tests/test_youtube_search.py) mocks the
YouTube API client and asserts `safeSearch="none"` is present in both call sites' kwargs, using
the real case that surfaced this — Berlin's "Sex (I'm A...)" — as the fixture data. It's a pure
mock (no live API/OAuth needed), so it runs deterministically; it fails immediately if either
`safeSearch="none"` argument is dropped in a future edit.

## A contaminated DB `title` field defeats matching before scoring even runs

`score_result()`'s bracket-stripping (`_relevance_text()`) only helps if the junk is bracketed.
Four `Dawid Zły` songs had raw, un-bracketed YouTube video-title cruft saved directly in the DB
`title` field (e.g. `"Timczasã [WIDEO] gòsc. MeHow Front KaszubskiHipHop.pl"` instead of just
`"Timczasã"`) — almost certainly from `download_youtube_video()` naming files after the raw video
title, then import falling back to `extract_unknown_data()`'s filename-split when there were no ID3
tags, with nothing downstream stripping the channel branding. This caused two distinct symptoms:
- The overly-specific, garbled query returned **zero** search results even though the real song
  exists on YouTube (`"Swòji zdanié WIDEO Kaszubski HipHop"` — nothing matched).
- Worse, when it *did* return candidates, the shared branding text (`"KaszubskiHipHop.pl"`) is
  present in every video's real title from that channel — i.e. in every *other* song by the same
  artist too — so it inflated similarity against the **wrong** Dawid Zły song and won the match.

This is not a `score_result()` bug the way the bracket/hashtag case was — it's contaminated input
data defeating a matching function that assumes the DB title is the actual song title. Cleaned
directly in `music.db` (ids 2780, 2790, 2795, 2797); if this recurs for other artists, look for the
same import path (no ID3 tags + `extract_unknown_data()`'s raw filename split) rather than trying to
patch `score_result()` to guess which part of a title is branding.

## A collab track credited in the DB solely to the featured/guest artist defeats matching entirely

Same category as the contaminated-title case above — bad `songs`/`artists` data defeating a
matching function that assumes the DB's artist attribution is the one the real upload actually
uses, not a `score_result()` bug. Real case: `Gaba Kulka - Biegnij dalej sam`. The DB credited the
song to `Gaba Kulka` alone (her only song in the whole database), but the track is actually a
`Fisz Emade Tworzywo` release on which she's a guest vocalist — confirmed via yt-dlp's official
track metadata on the real upload (`cQ_wNp43ulM`, 247K views): `"Provided to YouTube by Agora
Digital Music"`, `artists: ["Fisz Emade Tworzywo"]`, no mention of Gaba Kulka anywhere in YouTube's
own credit. Every real candidate's title/channel says "Fisz Emade Tworzywo", never "Gaba Kulka", so
`MIN_ARTIST_RELEVANCE` correctly rejected all of them — the *only* candidate that ever
title-contained "Gaba Kulka" was an unrelated song by a different band (`HEY`) that happens to
feature her too, and even that only cleared relevance 0.25, below the gate.

No scoring change can fix this — `artist_synonyms` doesn't apply either, since "Fisz Emade
Tworzywo" isn't an alias of "Gaba Kulka", it's a different (real, already-existing) artist row that
happens to also credit her as a guest. The actual fix is re-pointing `songs.artist_id` at the
correct artist via `merge_artists_in_db()` (`database_management.py`) — reassigns the song and
deletes the now-empty source artist row in one step, the same helper the app's own merge-artist
menu flow uses, rather than a raw `UPDATE` (see the Database safety section of `AGENTS.md` before
any direct write like this: check `ps aux` for a running `main.py` first). Once repointed to `Fisz
Emade Tworzywo`, `search_video_ytdlp()` resolves the real upload immediately, no other change
needed. If a search keeps failing even after every relevance/quality signal checks out, check
whether the DB's credited artist is actually who YouTube itself credits the track to before
assuming it's a scoring gap — a plausible-looking single-artist credit that's actually a
mis-attributed feature/collab is invisible to every mechanism in this file, the same structural
blind spot the `synonyms` column and this note both exist to flag, not fix automatically.

## HQ_KEYWORDS matched overlapping substrings, double-counting one signal as two

`quality` scoring looped every keyword in `HQ_KEYWORDS`/`VIDEO_KEYWORDS` independently and summed
`+2`/`-2` per hit — but `"official audio"` is itself a match for the separate `"audio"` entry in
the same list (same for `"official mv"` containing `"mv"` in `VIDEO_KEYWORDS`), so any title saying
"Official Audio" scored `+4`, not the intended `+2`. Combined with `_title_containment()` giving
every version of a short, exact title (e.g. "2001") identical `1.0` relevance regardless of
decoration, the tiebreak collapses to `quality` alone — so a `"(Dan Carey Dub) – Official Audio"`
reupload outscored `Foals`' actual official video (`quality=4` vs. `quality=-2`) and got added to
the playlist instead. Fixed two ways in `score_result()`:
- `_non_overlapping_hits()` drops a keyword hit that's wholly contained inside another hit from the
  same list, so `"official audio"` no longer also counts as a separate `"audio"` hit.
- The HQ bonus is withheld entirely when the title also matches an `LQ_KEYWORDS` alternate-version
  term (remix/dub/demo/session/live/...) — being professionally released as "Official Audio"
  doesn't make a remix the plain studio version, so it shouldn't cancel out that penalty.
  `LQ_KEYWORDS` also gained `"demo"`, `"dub"`, `"orchestra"`, `"orchestral"`, which weren't
  previously covered despite being the same class of signal as the existing `"remix"`/`"cover"`.

Caveat worth knowing if this surfaces again: `artist_relevance` (channel-name match) is compared
*before* `quality` in the sort tuple, so an alternate version uploaded to the artist's own official
channel (e.g. a live session) can still beat a better-quality version from an unofficial channel —
fixing the double-count stopped the remix from winning, but the actual top pick for `Foals - 2001`
becomes an official-channel live session, not necessarily the plainest fan upload. That's a
deliberate design tradeoff (prefer the official channel), not something this fix attempted to
change — don't over-tune `LQ_KEYWORDS` weights trying to force one specific video to win.

**Future redesign trigger:** if more cases like this keep surfacing — a signal that should
obviously lose (a live/remix/alternate version) still winning because some *other* term in the sort
tuple happens to be stronger for that particular candidate — that's a sign the additive,
lexicographic-tuple scoring model itself needs reconsidering (e.g. one blended score instead of
`(relevance, artist_relevance, quality)` compared strictly left-to-right), not another one-off
keyword/weight tweak. This has now recurred in a second, different form — see the popularity
section below — which makes it more likely a real pattern than a one-off. One-off keyword additions
are still the right fix for a single missing *vocabulary* signal (as `demo`/`dub`/`orchestra` were
here); it's specifically the *tiebreak losing despite the right signal being present* that's the
design problem.

**Update:** this recurred enough more times that a decision was made about it — see "This
tuple-ordering failure has now recurred five times" in the multi-artist-punctuation section below
for the current answer (a full blended-score redesign was considered and declined for now; a
smaller margin/dominance override is the recommended next step if it recurs again in a new shape,
not the full blend this note originally proposed).

Regression tests: [tests/test_youtube_search.py](../../tests/test_youtube_search.py) —
`test_score_result_does_not_double_count_official_audio_keyword` asserts the exact `quality` value
for the Dan Carey Dub title, and `test_search_video_ytdlp_does_not_pick_the_remix_for_foals_2001`
replays the real yt-dlp candidate list (mocked `subprocess.run`) and asserts neither remix wins.

## Query text itself can defeat search — extra words aren't free context, they can actively hurt

The query sent to both yt-dlp and the Data API used to be `f"{artist} - {title} audio"`. Dropping
the trailing `" audio"` (now just `f"{artist} - {title}"` in both `search_video_ytdlp()` and
`search_video()`) fixed two unrelated failures by itself, with no scoring change involved:
- `Passive Voice - Тебе пам'ятаю`: "Passive Voice" reads as a generic English grammar term, and
  adding "audio" made YouTube's search drift entirely to unrelated grammar/audiobook content — the
  real candidates weren't in the result set *at all* with "audio" in the query, but appeared
  immediately without it.
- `Ten Preston feat. Sitek - 71 (prod. lil aloes)`: the real video (3.26M views, public, no
  restrictions) didn't appear in results at any of 5+ query phrasings tried, including a widened
  `ytsearch20`, as long as "audio" was in the query. Removing it alone put the real video first.

Lesson: a query is not "safer" for carrying extra descriptive words — YouTube's search relevance
can drift or hide legitimate high-view results because of one added generic word, and this isn't
predictable from the query text alone (there's no obvious reason "audio" should hide a 3M-view
video). Keep constructed queries as close to the literal `"{artist} - {title}"` as possible; don't
add qualifying words "for context" without verifying against a real search first.

## Popularity (view count) as a quality signal — free from yt-dlp only, and not a strict "not-live" proxy

`score_result()` takes an optional `view_count` (only `search_video_ytdlp()` passes it — yt-dlp's
JSON includes it for free, but the Data API's `search().list()` doesn't return it, and getting it
there needs a separate `videos().list()` call per candidate, burning quota, so API-sourced
candidates just don't get this term). `_popularity_bonus()` log-scales it, centered on ~1000 views
(`log10(view_count) - 3`), to land in roughly the same range as the existing keyword-based
adjustments rather than swamping them.

This fixed `SLAUGHTER TO PREVAIL - Bratva`: relevance tied at 1.0 between the real track and an
"(Instrumental)" reupload, and `artist_relevance` — pure whitespace-formatting noise ("Prevail-
Bratva" vs "Prevail - Bratva") — happened to favor the instrumental *before quality was even
consulted* in the sort tuple, the same structural issue flagged above for `Foals - 2001`. Popularity
(8.3M views vs. 60K) was strong enough to flip this one, unlike a plain `-1` "instrumental" penalty.

It is **not** always a reliable proxy for "not a live/alternate version" on its own, though —
`OBERSCHLESIEN - Król Olch`'s Woodstock festival recording had *more* views (5.1M) than the actual
studio video (1.4M), so it kept winning even after being correctly tag-flagged as live and
penalized. Initially left as a known, open limitation rather than force-fitting a bigger constant
for this one case — see "`VIDEO_KEYWORDS`/`LQ_KEYWORDS` rebalanced" below for how it was eventually
resolved (as a side effect of a more general rebalancing, not a value tuned specifically for this
song).

Also left open, unrelated to popularity: `NIZKIZ - Правілы` picked a 67-view excerpt over the
74K-view real upload because the real upload spells "Правілы" the Russian way (missing the
Belarusian "і"), scoring relevance 0.55 vs. the excerpt's exact-match 1.0 — relevance is compared
*before* quality/popularity in the sort tuple, so popularity is never even consulted for this one.
A spelling-variant/orthography problem, not a popularity or quality-weighting problem. Distinct
from the diacritic-folding fix below: Belarusian "і" (U+0456) doesn't NFKD-decompose into Russian
"и" + a mark — it's a genuinely separate base letter, not an accent variant, so folding can't help.

## Artist identity that no string algorithm can bridge — solved via the `synonyms` column

Four real cases shared one root problem: the real channel/title uses a genuinely *different name*
for the artist than the DB does — not a spelling variant of the same name — so no amount of fuzzy
string matching (containment, similarity, diacritic-folding) could ever close the gap on its own:
- `Бумбокс - Нездара`: real channel `familyboombox` (an English fan-channel name), sharing zero
  characters with the Cyrillic artist name `Бумбокс`.
- `Плач Єремії - Вона`: the band is fronted by (and uploads under) Taras Chubai's own name.
- `Hall & Oates - Maneater`: real channel `Daryl Hall & John Oates` (full names), not the DB's
  shortened stage name.
- `Junecapone - Depravity`: real (Topic) channel just `June`, not the DB's fuller stage handle.

The `artists` table already has a `synonyms` column (`src/utils/database/datatables.py`'s `Artist`
model) that was essentially unused before this — read in exactly one unrelated place
(`discoveries_manager.py`, as an album-lookup fallback name) and, at the time, never populated by
any current code path (only set once at artist-creation time via mp3-tag import). It's a plain
string with no enforced format; comma-separated is now the convention for multiple aliases
(`_parse_synonyms()`). It can now also be appended to after the fact from
`Enter database -> Fetch artists -> Add synonym` (`src/menu/artist_actions/__init__.py`), which
dedupes case-insensitively against what's already there rather than overwriting it.

`score_result()` takes an optional `artist_synonyms: str | None` and checks `artist_relevance`
against the primary DB artist name *and* every parsed synonym, taking the max
(`_artist_relevance_for()`, called once per name). This flows from `create_yt_playlist()` reading
`entry.get("synonyms")` from the cache, which `init_cache()` (`yt_cache.py`) now populates from
`song.artist.synonyms` — i.e. the fix only takes effect once the DB row's `synonyms` field is
actually filled in for that artist; the mechanism alone doesn't do anything for an artist with no
synonyms recorded. Verified all four cases jump to `artist_relevance == 1.0` once the synonym is
supplied (previously `0.0`–`0.69` depending on how much accidental character overlap there was).

**Not fixed by this**: `Stray Kids - 특(S-Class)` stays open — that's a *title*-formatting mismatch
(Hangul mixed with a Latin parenthetical), not an artist-name problem, so the synonym mechanism
doesn't apply to it. See the next section for the title-level equivalent of this mechanism — it
doesn't cover Hangul yet either, but is the right place to add it. Should resolve to
[youtu.be/JsOOis4bBFg](https://youtu.be/JsOOis4bBFg) once someone gets to it.

## Title identity across scripts that no string algorithm can bridge — `transliteration.py`

The title-level version of the artist problem above: a song's DB title and its real YouTube
upload's title can legitimately be written in *either* of two alphabets (native script vs. a
romanization), and which one the real upload uses isn't predictable per song. Real cases —
Akute's `Cicha, jak maja śmierć` and `Ihołki` — have their DB title in Lacinka (Belarusian Latin
script), but the real official upload (confirmed via yt-dlp's `track` metadata, channel
`akutemusic`) is titled in Cyrillic (`Ціха, як мая смерць`, `Іголкі`). That upload scored only
0.16–0.20 relevance against the Lacinka DB title — well below the acceptance bar — so it never
even reached the quality/official-release tiebreak; the only candidates that *did* clear relevance
were live bootleg reuploads whose titles happened to already be in Latin script. No amount of
`similarity()`/diacritic-folding tuning closes a whole-alphabet gap, the same reason the `synonyms`
column exists for artist names — except a DB-column-per-song approach doesn't scale here the way it
does for artists, because the relationship between the two spellings is systematic (a real
transliteration scheme), not an arbitrary alias.

`transliteration.py`'s `TRANSLITERABLE_LANGUAGES` registry (keyed by the `songs.language` column,
lowercased) maps a language to a `to_latin`/`to_native` function pair; currently only
`"belarusian"` is registered (`belarusian_to_latin()` / `latin_to_belarusian()`, an
Instruction-2000-style Cyrillic↔Lacinka table). `transliterate(title, language)` picks the
direction automatically by detecting which script `title` is already in. The letter-level rules
have real, non-obvious phonological gotchas — regressive softness assimilation cascades *backward*
through a whole consonant cluster to whatever я/е/ё/ю/і/ь follows it (`сне` → `śnie`, `смерць` →
`śmierć`: `с` softens even two letters back from `е`, across the unmarked `м`), except `л`, whose
hard/soft split (`ł`/`l`) is always determined by its own immediate next letter and never cascades
in from later in its cluster (`Іголкі` → `Ihołki`, not `Iholki`), and a hard apostrophe blocks the
cascade entirely rather than triggering it the way `ь` does (`з'ява` → `zjava`, not `źjava`). See
that file's docstrings and `tests/test_transliteration.py` for the full derivation — every rule was
derived from a specific real-word counterexample, not assumed up front. The reverse direction
(Lacinka → Cyrillic) has one accepted, unrecoverable ambiguity: a soft marker (`ś`/`ń`/`ć`/`ż`)
immediately before another letter could have come from a real `ь` *or* from cluster assimilation
with no `ь` at all (`śnie` → `сне`, correctly no `ь`) — since the Latin spelling alone can't
disambiguate, `ь` is only reinserted when the marker is at a genuine word boundary (nothing follows
at all); this occasionally drops a real `ь` on reversal (`piśmieńnikami` → `пісменнікамі`, missing
one `ь`), accepted as fine for a search-query generator, not acceptable if this were ever repurposed
for canonical spelling storage.

The retry itself lives in `search_video_ytdlp()`/`search_video()` (`manage_youtube_playlists.py`):
triggered when `is_transliterable(language)` **and** (nothing cleared the relevance bar, **or** the
winning pick isn't a confirmed official release per `_is_official_release()`). That second
condition was the one that actually mattered in practice — an earlier version gated the retry on a
numeric relevance floor (the theory being "only retry when the original search found nothing even
loosely relevant"), but real testing against Akute's catalog showed the actual failure shape is the
opposite: the original search *succeeds* at relevance 1.0 (a live reupload's Latin title matches
fine), so a pure "did it fail" trigger never fires at all, no matter how the floor is tuned. Once a
result is found, retrying only when it isn't a confirmed official release is what actually catches
the case — confirmed fixed for `Cicha, jak maja śmierć` end-to-end. **Known limitation**: yt-dlp's
anonymous search is non-deterministic (see below) — the exact transliterated query that found
`Ihołki`'s official upload in one run returned zero results moments later in the same session; when
the alt-script attempt also comes up empty, the original (possibly wrong) pick is kept rather than
returning nothing, which is correct behavior but doesn't guarantee the fix engages on every run.
Not yet handled: non-Cyrillic scripts (Stray Kids' Hangul case above) — `to_native` is `None`-able
in `TransliterableLanguage` specifically so a future one-directional-only language (e.g. Japanese
romaji, where kana/kanji reconstruction isn't well-defined) can register without needing a reverse
function.

## `tags` scanning is scoped to `STRONG_LQ_KEYWORDS` only — generic tags get SEO-stuffed

A live recording's *title* often carries no signal at all —
`"Oberschlesien - Król Olch #Woodstock2016"` has no English "live"/"concert" word — but yt-dlp's
free `tags` field did: `"Oberschlesien Król Olch na żywo"` ("na żywo" is Polish for "live"), plus
repeated festival names, while the actual studio upload's tags were clean. That held up — but
**not universally**: `Elvis Crespo - Suavemente`'s real official Vevo upload's tags include
generic, broadly-cast SEO terms (`"remix"`, `"karaoke"`, `"instrumental"`, `"en vivo"`/`"en
directo"` — Spanish for "live") that don't describe *this* upload's content at all, just adjacent
searches the label also wants to rank for. Scanning those against the full `LQ_KEYWORDS` list
wrongly penalized the real video (299M views) — and, since an `LQ_KEYWORDS` hit suppresses the
whole HQ bonus block, cost it the `"official"` bonus too, a big enough swing to lose to an
unrelated collab reupload with far fewer views (19.6M).

`HQ_KEYWORDS`/`LQ_KEYWORDS`/`VIDEO_KEYWORDS`/`HIGH_TRUST_KEYWORDS` now scan the **title only**
(`title_lower`). Only `STRONG_LQ_KEYWORDS` (and the remix-selector-hint bonus, see below) still
scans `title + tags` (`keyword_text`) — the distinction is deliberate, not arbitrary: a channel is
unlikely to blanket-SEO-tag something as specific as `"live"`/`"na żywo"`/`"woodstock"` onto an
unrelated upload the way it will tag generic descriptor words like `"remix"`/`"karaoke"` for reach.
(That said, this line of reasoning has a known crack — Suavemente's own tags *do* include `"en
vivo"`, so adding Spanish live-synonyms to `STRONG_LQ_KEYWORDS` in the future could reintroduce the
same false-positive on this exact song. Not added preemptively; only add a language's "live" term
to that list once a real case actually needs it, and re-check against Suavemente's tags first.)
Deliberately `tags` only, never `description`: tags are curated keywords an uploader picked,
description is freeform prose where "live" could appear in unrelated boilerplate (tour dates,
etc.) and false-positive even worse. `LQ_KEYWORDS` gained `"na żywo"` for the original fix.

`HIGH_TRUST_KEYWORDS`/`HIGH_TRUST_BONUS` (currently just `"oficjalny odsłuch albumu"` — Polish for
"official album listen", a label's official full-album premiere upload) is a separate list from
`HQ_KEYWORDS`, scored at `+4` instead of the generic `+2`, for phrases specific enough to be an
unambiguous "this is the real official upload" signal rather than a generic quality indicator like
"HD"/"remaster". Keep it that way if extending it — don't fold new entries into `HQ_KEYWORDS` just
because they're both "good" signals; the point of a separate list is the stronger, deliberately
uneven weight.

## `VIDEO_KEYWORDS`/`LQ_KEYWORDS` rebalanced — a uniform penalty per list was too coarse

Two problems from a single root cause: every `VIDEO_KEYWORDS` hit cost the same `-2` regardless of
how bad the alternate-format upload actually was, and every `LQ_KEYWORDS` hit cost the same `-1`
regardless of how bad the alternate arrangement actually was. In practice these aren't uniform:
- The video-format penalty was heavy enough that the *correct, most popular, officially-uploaded*
  video could lose to an obscure alternate cut purely for being a video instead of audio-only
  (`Måneskin`'s 230M-view official video losing to an 8.9M-view "Eurovision Version"; `Daði Freyr -
  Bitte`'s official video only barely surviving a 10x-less-popular live recording, 0.372 vs 0.368).
  Lowered to `-1` (`VIDEO_PENALTY`).
- `"live"`/festival-recording signals deserved a heavier penalty than the rest of `LQ_KEYWORDS` — a
  live recording is essentially never the plain studio version, unlike, say, an "acoustic" cut,
  which occasionally *is* the canonical release. Split `"live"`/`"na żywo"` out into their own
  `STRONG_LQ_KEYWORDS` list at `-3` (`STRONG_LQ_PENALTY`), separate from the generic `-1`.
  `"woodstock"` joined this list too (Poland's Pol'and'Rock/Woodstock festival implies a live
  performance) — a plain lowercased substring check already catches every form ("Woodstock",
  "#Woodstock2016", "woodstock") without listing each variant.

Together these fixed `Daði Freyr - Bitte` decisively (not just barely) and — as a side effect, not
a value tuned specifically for it — resolved the `OBERSCHLESIEN - Król Olch` "viral live clip
out-views the studio original" case flagged as an open limitation above: the studio video no longer
loses ~2 points to the video penalty, and the live clip now loses 3 points per matched strong-LQ
term (both `"na żywo"` and `"woodstock"` matched its tags, so `-6` total) rather than `-1`.

## A DB title's bracket content can be a meaningful selector, not just junk to discard

`_relevance_text()` still strips all bracketed text for the main relevance comparison (unchanged —
most of it really is disposable annotation). But sometimes it's the opposite: DB title `"Get Back
(Lorin Rymbu & Denis Rynda Remix Extended)"` wants *that* remix specifically, not just any version
— stripping it for relevance meant every remix (right or wrong) scored identical `1.0`, so a
completely unrelated remix (`"Deepshader's Reconstruction"`) won on quality/popularity alone.
There's no reliable way to tell a meaningful selector from junk annotation by looking at the
bracket in isolation (`"[Official Video]"` and `"(Lorin Rymbu & Denis Rynda Remix Extended)"` are
syntactically identical), so instead of guessing up front, `_bracket_selector_hints()` keeps what
`_relevance_text()` discards, and `score_result()` checks it *separately*, after relevance, against
the candidate's own (unstripped) title/tags — a real match earns `SELECTOR_MATCH_BONUS` (`+3`) as a
tiebreak, not a relevance change, so a search with no matching-remix candidate still falls back to
whatever's available instead of rejecting everything.

The false-positive risk (rewarding *any* candidate that happens to share generic branding like
"Official Video") is handled by `_selector_tokens()`: it reduces a hint to only its specific,
identifying words via `_SELECTOR_STOPWORDS` (built from every existing keyword list's words, plus
common remix vocabulary like "remix"/"extended"/"feat"). A plain `"Official Video"` hint reduces to
zero tokens and is never treated as a selector at all; `"Lorin Rymbu & Denis Rynda Remix Extended"`
reduces to the actual names (`{"lorin", "rymbu", "denis", "rynda"}`). Also note the match is on
those *words*, not the whole bracket phrase verbatim — the real matching candidate's title says
just "Remix", not "Remix Extended", so a naive whole-phrase containment check would have missed it.

## `"official"` is a trust signal independent of the audio/video format preference

`VIDEO_KEYWORDS`' format penalty and "is this genuinely the official upload" used to be conflated —
only the format penalty was tracked, so a video correctly labeled `"[Official Music Video]"` scored
*worse* than a random, unlabeled fan upload that said nothing distinguishing at all. This surfaced
starkly on `Foals - 2001`: a bare-number title where every candidate ties at `1.0`
relevance/artist_relevance via `_text_containment` (the number/artist name trivially appears
everywhere), so `quality` alone decided, and the real official video (`-1`, video penalty with no
offsetting signal) lost to several candidates that said nothing at all and coasted to `0`. Adding
bare `"official"` to `HQ_KEYWORDS` (distinct from the already-existing `"official audio"` entry,
which still dedupes against it via the existing substring-containment rule in
`_non_overlapping_hits()`) fixed this: the real video now scores `+1` (a genuine `+2` trust bonus,
still `-1` for being a video), clear of everything else. Stays correctly suppressed on
remixes/dubs/demos exactly like `"official audio"` already did — verified none of the wrong
`Foals - 2001` candidates (Myd Remix, Dan Carey Dub, the orchestral collab, all of which also say
"official" somewhere) picked up the bonus, because each also matches an `LQ_KEYWORDS` term that
suppresses the whole HQ bonus block.

## Typo/spelling-variant tolerance for short titles: doubled letters and diacritics

Two related relevance-matching gaps, both stemming from the same root cause: the scaled threshold
that protects short titles from false positives (the "netflix ≈ nix" problem above) also makes
them brittle to a single-character legitimate spelling variant, because one differing character is
a much larger fraction of a short string's `similarity()` ratio than of a long one's. Rather than
lowering the threshold (which would reopen the false-positive risk it exists to prevent), both are
handled as extra candidates in the same `max()` already used for relevance — applied symmetrically
to both sides, so each only *adds* a way for a legitimate variant to match, never loosens what
already matched. Title relevance only (not `artist_relevance`) — scoped to the problems actually
observed.

- `_collapse_repeated_letters()` folds runs of 2+ identical letters to one (`"Pompeii"` →
  `"Pompei"`). Real case: `Bastille - Pompei` (DB) scored relevance `0.50` against the real
  `"Pompeii"` — needed `0.85` for a title this short — purely from the missing second "i".
- `_fold_diacritics()` strips accents via Unicode NFKD decomposition + dropping combining marks —
  the same technique `normalizer.normalize()` already uses elsewhere in the app, reused here rather
  than pulling in that function's other transformations (full punctuation removal, lowercasing
  done elsewhere already). Real case: `ABRADAB - Niesmiertelnosc` (DB, ASCII) against the real
  `"Nieśmiertelność"` — exact containment couldn't bridge the missing "ś"/"ć", and the fallback
  `similarity()` ratio, further diluted by an "ABRADAB - " prefix on the candidate side, dropped
  low enough (0.6) to lose to an unrelated live recording (0.8).
  - This isn't limited to Latin accents — NFKD decomposition also covers some same-script letter
    variants that read as "the same base letter plus a mark" under Unicode even when they don't
    look like an accent to an English speaker. Real case: `bayski - набіраи` (DB, a typo — Cyrillic
    "и") against the real `"набірай"` (Cyrillic "й", short I) — "й" decomposes to "и" + a combining
    breve, so folding fixes the typo too, with no Cyrillic-specific rule needed.
  - Not universal, though: Belarusian "і" (U+0456, as in `NIZKIZ - Правілы`, see above) does *not*
    NFKD-decompose into Russian "и" + a mark — it's a genuinely separate base letter, not an accent
    variant, so this fix can't help every Cyrillic spelling-variant case, only the ones that are
    truly "a letter plus a diacritic" under Unicode.

Both transforms are combined into one "maximally normalized" pass (fold diacritics, then collapse
repeated letters) rather than kept as separate `max()` branches — a title can need both at once,
and running them together costs nothing extra since neither transform does anything when its own
pattern isn't present.

## `track`/`artists`/`album` — a free, reliable "genuine official release" signal from yt-dlp

`_is_topic_channel()`'s "-topic" channel-name suffix check turns out not to be reliable: yt-dlp's
`channel` field doesn't always carry it even for a genuine auto-generated Topic channel. Verified
directly against real yt-dlp output — `Al Di Meola`'s Topic channel reports `channel: "Al Di
Meola"` (no suffix at all), `SunSay`'s reports `channel: "sunsaymusic"`, while `Вольны Хор`'s
reports the literal `"Вольны Хор - Topic"` — the same underlying kind of channel, three different
reported forms, even though the YouTube *website* displays all three as "\<Artist\> – Topic". Real
case: `Al Di Meola - Double Concerto` — the actual official upload (`channel="Al Di Meola"`, 10.9K
views) lost to a live Budapest recording (91K views, no English "live"/"concert" keyword in its
title) purely because the channel-suffix check silently didn't fire.

yt-dlp's search JSON separately exposes YouTube Music's own `track`/`artists`/`album` metadata,
populated *only* for a track actually distributed to YouTube by a label/aggregator — every such
candidate's `description` literally reads "Provided to YouTube by ..." / "Auto-generated by
YouTube.". A fan reupload, live bootleg, or cover never has it. Verified directly: across three
real cases (Al Di Meola above, `SunSay - В твоих глазах сияю я`, `SunSay - Немовля`), every wrong
production pick had `track=None`, every correct pick had `track` set. `_is_official_release(channel,
track)` grants the same `+2` quality bonus `_is_topic_channel()` used to grant alone, now on
*either* signal — `track` catches exactly the real Topic uploads the channel-name check misses,
without double-counting when both happen to agree.

## A channel *handle* concatenated with the artist name defeats whole-word containment

`_text_containment()` requires a whole-word match (`\bsunsay\b`) to avoid the "netflix ≈ nix"
false-positive problem — but a real channel handle often squeezes the artist name and a suffix
together with no separator at all: `SunSay`'s real channel is literally `"sunsaymusic"`, and the
already-documented Suavemente case (above) has an `"ElvisCrespovevo"` *tag* doing the same thing.
There's no word boundary between "sunsay" and "music", so containment can never match it — only a
diluted `similarity()` ratio (~0.71) applies. Real case: `SunSay - В твоих глазах сияю я` — a wrong
"acoustic" cover reupload, whose own *title* spells "SunSay" as a separate word, scored a full 1.0
artist_relevance there, while the genuine `sunsaymusic`-channel upload scored only ~0.71 — and
since `artist_relevance` sorts before `quality` in score_result()'s tuple, that alone decided the
match regardless of the `quality` gap (the acoustic cover is also LQ_KEYWORDS-penalized).
`_artist_channel_handle_match()` squeezes both sides (strips spaces) and checks a plain prefix
match — safe because it only ever adds a match for a channel that starts with the *exact* artist
name, never loosens an existing one.

## Comma vs. slash vs. plain spaces for a multi-artist DB field

A DB `artist` field for a collaboration and a candidate's own title/channel name often list the
same artists with different punctuation conventions. Real case: `Waglewski, Fisz, Emade - Bóg` —
the DB spells the trio with commas; the real Topic channel's display name uses slashes
(`"Waglewski / Fisz / Emade - Topic"`). Plain `similarity()` rates that punctuation-only difference
(`artist_relevance` 0.74) *below* an unrelated `"(live Frytka Off)"` reupload whose title happens to
spell the same names with plain spaces and no punctuation at all (0.83) — even though `quality`
correctly ranks the Topic channel far above the live recording (`+3.33` vs. `-2.77`, the same
official-release bonus above plus the existing `STRONG_LQ_KEYWORDS` "live" penalty). Since
`artist_relevance` sorts before `quality`, the live recording won anyway. This is the "SLAUGHTER TO
PREVAIL" tuple-ordering failure shape (see above) recurring a further time, in a new form
(separator punctuation, not whitespace). `_normalize_multi_artist_punctuation()` treats
comma/slash/ampersand as interchangeable separators — folded into `_artist_relevance_for()`'s
existing `max()` pattern, so it only ever adds a way to match.

**This tuple-ordering failure has now recurred five times** (Bratva/whitespace, Foals/quality
double-count, Waglewski/punctuation, La Vida Bohème/missing diacritic folding in artist_relevance,
Drenchill/a "ft." credit split across the song title) with five different one-off fixes. Each fix
was still the right call for its specific missing signal.

**Decision (discussed with the user directly, 2026-09-22): not a full redesign, at least not yet.**
The "Future redesign trigger" note above (written after the *third* recurrence) proposed replacing
the additive `(relevance, artist_relevance, quality)` tuple with one blended score once this kept
happening — that threshold has now been crossed twice over, but a full redesign was considered and
explicitly declined for now. Reasoning: every one of the five recurrences shares the same real
shape — `quality` was *already computed correctly* and already favored the right video by a wide
margin (e.g. Drenchill: `5.2` vs. `-1.6`; La Vida Bohème: `4.3` vs. `1.2`) — the bug was never bad
quality scoring, it was that a small or even trivial `artist_relevance` edge (`1.0` vs. `0.93`, or
`1.0` vs. `0.69`) blocked `quality` from ever being consulted at all. A full blended score is a
large, risky, all-at-once change against ~36 already-tuned real regression cases — re-deriving safe
combination weights across three heterogeneous scales (`relevance`/`artist_relevance` are `0`-`1`,
`quality` is roughly `-5` to `+8` unbounded) risks silently flipping cases that already work,
without a correspondingly large expected payoff (5 fixes across many months of real usage is not a
high recurrence rate).

**What to actually reach for if this recurs a sixth time in a new shape** (not another
missing-signal variant of one of the five above — those still just get their own one-off
`artist_relevance` fix, same as always): a much smaller, targeted structural change than a full
redesign — a *margin/dominance override* rule, e.g. "if `quality`'s gap between the top two
candidates exceeds some threshold, let it override a `relevance`-tied pair's `artist_relevance`
ordering" — rather than a strict lexicographic sort. This targets the actual common thread directly
(quality already knew the answer, artist_relevance just cut it off too early) without touching how
`relevance`/`artist_relevance`/`quality` are each computed, and is a far smaller, more reviewable
change than re-deriving one blended score from scratch. Raise this specific option with the user
before implementing it — don't default straight to the full-blend idea from the older trigger note,
and don't implement either one unprompted.

## Content *about* the song can defeat the relevance gate entirely — `NOT_THE_SONG_KEYWORDS`

`LQ_KEYWORDS` only ever costs a candidate a `quality` tiebreak — and a tiebreak only matters when
something else ties its `relevance` for `quality` to be consulted against. That assumption breaks
when the *only* candidate that title-matches at all is the wrong kind of content. Real case: `Taco
Hemingway - Fuck Your List` — the real official upload is age-restricted and excluded from YouTube
search results entirely, even for an authenticated Data API request (see below), so a
`"Tłumaczenie Taco Hemingway - Fuck Your List | LyricsTranslationTV"` video (a lyrics *translation*,
not the song) was the only candidate whose title contained the exact expected title — no `quality`
penalty, however large, could stop it from winning, since nothing else tied its relevance.
`NOT_THE_SONG_KEYWORDS` (currently `"tłumaczenie"`, `"lyrics translation"`) forces `relevance` to
`0.0` outright for a title matching one of these — a disqualification, not a tiebreak penalty, and
deliberately a separate list from `LQ_KEYWORDS`: unlike a remix/cover/live version (still a real
recording of the song, just a different arrangement), a translation/reaction-to-lyrics video isn't
the song at all.

## `MIN_ARTIST_RELEVANCE` — a floor under the (deliberately title-only) relevance gate

Disqualifying the translation video above surfaced a second problem in the same real case: the
next-best title match, once that one's gone, was `"Fuck ya list"` by `"Paccman chico"` — a
completely unrelated song by a completely unrelated artist, `relevance` ~0.85 from coincidental
character overlap with "Fuck Your List" alone. The title-only relevance gate (see "Why title-only"
above) has no way to catch this on its own — that's the tradeoff it was designed around. Rather
than folding artist name into the relevance computation (reopening the exact failure mode that
design avoids), `MIN_ARTIST_RELEVANCE` (`0.35`) is a second, independent gate: a candidate must
clear *both* the title-relevance bar and this artist floor to be picked at all. 0.29
(`artist_relevance` for the wrong Paccman chico pick) sits below it; every legitimate candidate's
`artist_relevance` across the whole regression suite sits comfortably above it (checked directly —
the lowest deliberately-matching case is ~0.5). Enforced in one place, `_select_best_candidate()`,
shared by both the yt-dlp and Data API search paths so they reject the same way — and both now
return `(video_id, best_relevance)` so a caller (e.g. the transliteration retry in
`transliteration.py`) can see how close the closest *artist-plausible* candidate got.

## yt-dlp's anonymous search is confirmed non-deterministic run-to-run

`search_video_ytdlp()` now retries once (`max_attempts=2`, no backoff) on a `returncode != 0` or
empty-stdout response before giving up. Not a wider net — a transient-failure retry. Real case:
`Waglewski, Fisz, Emade - Bóg` returned **zero** results in one production run (forcing a fallback
to the Data API, which only had the `"(live Frytka Off)"` reupload to offer — see the sort-order
case above), but returned **7** good candidates, including the correct Topic-channel upload,
moments later on an identical retry with the same query. One cheap retry is enough to survive a
momentary hiccup; a real, sustained outage still falls through to the API fallback as before.

## Age-restricted content is excluded from YouTube search results even for an authenticated request

The already-documented `"<name> Sex ..."` case (above) was diagnosed as an anonymous-scraping/
`safeSearch` problem, fixed by passing `safeSearch="none"` on the Data API call. `Taco Hemingway -
Fuck Your List` looked like the same category at first — but is a materially different, *stronger*
limitation: the real video (age-restricted per YouTube's own community guidelines) is invisible to
**every** yt-dlp `player_client` option tried (web, tv_embedded, android, ios, mweb — none surface
it), and, verified directly with this project's own authenticated OAuth credentials
(`get_youtube_service()`, real API call, not a mock), is **also absent** from the Data API's
`search().list()` results with `safeSearch="none"` already set. It's the #1 organic result on
youtube.com's own search for a signed-out browser session, but neither search backend this app
uses returns it. There is no code-level fix for this — no query phrasing, `player_client`, or API
parameter bypasses it (confirmed empirically, not theorized).

The only real fix is `Song.youtube_video_id` (see `docs/data-model.md`) — originally built as a
manual, human-set override, it's since become dual-purpose: `create_yt_playlist()` also writes to
it itself, via `save_video_id_to_song()`, the moment a fresh search succeeds, so *every* song's
video only ever needs to be found once, not just the ones a human manually confirmed. `yt_cache.py`'s
`init_cache()` still pre-fills a song's cache `video_id` from it, but `create_yt_playlist()` no
longer trusts that value blindly — `is_video_id_valid()` (`yt-dlp --simulate`, no API quota)
confirms it still resolves before reuse, since a video can go stale (deleted, made private) long
after it was set; a failed check falls back to a fresh search instead of silently keeping a dead
link. If that fallback search also comes up empty, `save_video_id_to_song()` is called again with
`video_id=None` to wipe the dead link from the DB (and the cache entry) — otherwise it would sit
there forever, silently re-failing the same `is_video_id_valid()` check on every future run instead
of ever getting a real second chance at a fresh search. Reach for a *manual* set of this field
whenever a song's correct video is confirmed unreachable by automated search (age-restriction is
the one confirmed trigger so far, but the mechanism doesn't assume that's the only possible cause)
rather than trying to tune scoring around a candidate pool that structurally can never contain the
right answer. Every reuse, validation failure, search miss, and DB save in this flow is logged to
`youtube_link_cache.log` (repo root, gitignored, always-on regardless of the `.debug` flag that
gates `debug.log`) — check it first if a playlist run picks an unexpected video or a stored link
stops working.

A separate, human-only annotation — `manage_youtube_playlists.NO_VIDEO_SENTINEL` (the literal
string `"N/A"`) — can also be written to `Song.youtube_video_id` to record "checked, this song has
no video on YouTube in any form" (as opposed to the above, where a real video exists but is
unreachable by search). Writing it is a menu action (`report_no_yt_video()` in
`menu/song_actions/__init__.py`, "Report no YouTube video" in the songs action menu), but
*consuming* it is still data-only: `create_yt_playlist()` doesn't skip on it — it normalizes
`"N/A"` to "no stored link" and runs a normal search every time, exactly as if the field were
empty, because *is_video_id_valid()* would just fail on a non-id string and there's no mechanism
yet to have the sentinel actually short-circuit the search. If a future change adds that skip
behavior, this is the place both to add it and to update.

The songs action menu separately has a "Remove links" option (`remove_links_from_songs()` in
`menu/song_actions/__init__.py`), but it clears `youtube_video_id` back to `None` (plain "not
searched yet"), **not** the `"N/A"` sentinel above — those mean different things (retry on the next
playlist run vs. "confirmed no video, don't bother"), so use the right one for the intent.

## YouTube Data API quota budget, and why `add_video_to_playlist()` must propagate `quotaExceeded`

Quota (standard project grant: 10,000 units/day) is the real ceiling on how many songs one playlist
run can process per day, and it's spent on two separate calls, not one: `playlistItems().insert()`
(50 units) fires once per song, unconditionally, the moment a video_id is found; the Data API
`search().list()` fallback (100 units, up to twice if the transliteration retry in
`transliteration.py` also fires) is skipped entirely whenever yt-dlp itself resolves the song, which
is the common case — `videos().list()` is deliberately never called at all (see the age-restriction
section above) specifically to avoid a third per-candidate cost. Roughly: ~200 songs/day if yt-dlp
resolves everything itself, down to ~40/day in the worst case where every single song needs both an
API search and a transliteration retry.

Because quota is shared across both calls, it can run out mid-`playlistItems().insert()` just as
easily as mid-search — and the two call sites used to handle that very differently.
`add_video_to_playlist()` re-raises `HttpError` when `is_quota_exceeded()` is true instead of
treating it as an ordinary permanent per-video failure, so it reaches `create_yt_playlist()`'s own
`except HttpError` block — the same "save progress, print a resume message, stop" path the
search-side quota check already used. `create_yt_playlist()` in turn only sets a cache entry's
`"added"` flag after `add_video_to_playlist()` actually returns `True`. Both matter together: since
`"added"` entries are skipped on resume, setting the flag unconditionally (the original bug) would
silently mark a song "done" the moment quota ran out mid-insert — or on any other insert failure —
and it would never be retried on a later run.

## A live TV performance show's own name can carry no "live" wording at all

Same failure shape as `"woodstock"` joining `STRONG_LQ_KEYWORDS` (see the rebalancing section
above), a second instance rather than a new mechanism. Real case: `Benjamin Clementine -
Cornerstone` — the real official video (`7Dc5BQ31iLw`, 6.7M views) lost to a BBC "Later... with
Jools Holland" TV performance clip (`CJJNl1p-PGA`, 1.2M views) by a narrow quality margin (5.06 vs
4.83), because:
- The official video's own `"(Official Video)"` label costs it `VIDEO_PENALTY` (`-1`) for being a
  video, per the existing tradeoff documented above.
- The Jools Holland clip's title (`"... - Later... with Jools Holland - BBC Two HD"`) mentions no
  English "live"/"concert"/"session" word anywhere — the show's own name is the only signal it's a
  live performance — so it took zero `LQ_KEYWORDS`/`STRONG_LQ_KEYWORDS` penalty, and even picked up
  an HQ bonus from `"HD"` (`"BBC Two HD"`).

`"jools holland"` joined `STRONG_LQ_KEYWORDS` (not the generic `LQ_KEYWORDS` list) since, like
Woodstock, a Jools Holland performance is never the plain studio version. Verified directly against
real yt-dlp output for this song: quality flips from `5.06` (wrongly won) to `0.06`, correctly
losing to the real official video's `4.83`. Other live-session shows already self-report via the
word "Live" in their own name/title convention (e.g. `"Live on The Tonight Show Starring Jimmy
Fallon"`, already caught by the existing `"live"` entry) — Jools Holland was the one seen in
practice that doesn't. Same whack-a-mole caveat as every other one-off keyword addition in this
file: add another specific show name only once a real case surfaces needing it, don't try to
enumerate every TV/session brand preemptively.

A second, near-identical case surfaced in the same batch of user-reported failures: `"when in
rome"` (Genesis' named 2007 live concert film/DVD) joined `STRONG_LQ_KEYWORDS` for `Genesis - Firth
Of Fifth`, where an 11.3M-view reupload of it took no LQ penalty at all and nearly won on view count
alone against the real official audio (2.9M views) — the official audio only survived that
particular run because it happened to still be in the fetched yt-dlp candidate pool.

## Keyword matching needed a word boundary — a keyword can be a substring of an unrelated word

Every `HQ_KEYWORDS`/`LQ_KEYWORDS`/`STRONG_LQ_KEYWORDS`/`VIDEO_KEYWORDS`/`HIGH_TRUST_KEYWORDS`/
`NOT_THE_SONG_KEYWORDS` check used to be plain `keyword in text` — safe for the multi-word phrases
these lists are mostly made of, but not for a short, single-word entry that happens to also be a
literal prefix of some other, unrelated word. Real case: `Al Di Meola - Double Concerto` — the real
Topic-channel upload (bare title `"Double Concerto"`, genuine `track` metadata) silently took
`LQ_KEYWORDS`' `"concert"` entry as a hit, because `"concert"` is a literal substring of
`"Concerto"` — an unrelated classical-music term, not a live-performance signal at all. This had
always been true, but stayed harmless as long as the official-release quality bonus applied
unconditionally (see the next section) — once that bonus was gated on "no LQ hit at all" to fix a
different real case, this latent false positive became decisive on its own, dropping the real
upload below an unrelated live recording.

`_keyword_present(keyword, text)` (a `\bkeyword\b` regex, Unicode-aware so accented-letter keywords
like `"na żywo"` still get real word boundaries) replaces every one of those `in` checks. This is
strictly more correct, not narrower — every keyword-list entry is meant to match as a real word or
phrase on its own, never as a fragment of a longer word, and the existing same-list dedup in
`_non_overlapping_hits()` (e.g. `"audio"` inside `"official audio"`, `"orchestra"` inside
`"orchestral"`) still works unchanged, since those are a whole word appearing inside a longer
*phrase* (a real word boundary exists at the space), not inside a longer single word. The one
adjustment this forced: `"react"` no longer substring-matches `"reaction"` under a word-boundary
check, so `"reaction"` joined `LQ_KEYWORDS` as its own explicit entry to keep that real, previously
relied-upon match (`Bloodywood`'s `"(REACTION)"` case, see below) working.

## The official-release quality bonus needed the same "don't offset an alternate-version penalty" gate as the HQ bonus

`_is_official_release()`'s `+2` bonus (genuine label/aggregator distribution, or a Topic channel —
see that function's own docstring) used to apply completely unconditionally, unlike the `HQ_KEYWORDS`
bonus right above it in `score_result()`, which is already withheld whenever an `LQ_KEYWORDS`/
`STRONG_LQ_KEYWORDS` hit signals an alternate arrangement (see the Foals - 2001 double-count section
above). Being genuinely, officially distributed doesn't mean being the plain canonical version — a
label can distribute a remix or club edit through the exact same Topic-channel/`track`-metadata
pipeline as the original release. Real case: `Betoko - Breaking (Original Mix)` — a `"(Club Edit)"`
upload had its own `track` metadata (a real official release, not a fan reupload) and this bonus
applied regardless, outscoring the real `"(OKO Recordings)"` upload (an ordinary artist-channel
upload, no official-release metadata at all) despite both tying on relevance/artist_relevance
(quality `1.65` vs `0.19` — wrong candidate winning). Fixed two ways together, neither sufficient
alone: `"club edit"` joined `LQ_KEYWORDS` (same category as `"remix"`), and the bonus itself is now
gated by `and not lq_hits and not strong_lq_hits`, mirroring the HQ bonus's existing gate exactly.

## Polish "ł" doesn't NFKD-decompose — `_fold_diacritics()` needed a direct substitution for it

Same non-decomposing-letter shape already documented above for Belarusian "і" (`NIZKIZ - Правілы`)
— Polish "ł" (U+0142) is a genuinely separate base letter in Unicode, not a base letter plus a
combining mark, so NFKD folding leaves it untouched. Unlike the Belarusian case, though, there's no
cross-script ambiguity here: an ASCII-typed DB title dropping "ł" always means plain "l" was
intended, so a direct substitution table (`_NON_DECOMPOSING_LETTER_FOLDS`, applied inside
`_fold_diacritics()` after the NFKD pass) closes this gap safely, the same "only ever adds a way to
match" guarantee the rest of this diacritic-tolerance machinery already relies on.

Real case: `Krzysztof Zalewski - Milosc Milosc` (DB, ASCII) vs the real `"Miłość Miłość"` — NFKD
folds `"ość"`'s `"ś"` to `"s"` fine, but the untouched `"ł"` meant the folded candidate was still
`"Miłosc"`, not `"Milosc"`, so exact containment never matched *any* candidate. This compounded with
the usual decoration-dilutes-similarity problem: every properly-labeled official candidate carried
an `"Krzysztof Zalewski - "` prefix, diluting the fallback `similarity()` ratio to ~0.47, while a
`"(Live)"` upload (bare title, no artist prefix at all) coincidentally scored `0.85` — closer to the
bar, and the one that won, purely from having nothing to dilute it. Folding `"ł"→"l"` restores exact
containment for every candidate equally (all back to `1.0` relevance), so `quality` — which already
correctly penalized `"(Live)"` — decides instead.

## `artist_relevance` needed diacritic folding too — it was only ever applied to title relevance

`_fold_diacritics()` existed for years before this was ever applied inside `_artist_relevance_for()`
— an oversight, not a deliberate scope limit. Real case: `La Vida Bohème - Radio Capital` (DB has
the accented "è"). The real official upload is hosted on a *label* channel ("Nacional Records",
contributing nothing to artist_relevance on its own), so its only artist-match signal was a
`similarity()` ratio against its own video title text (`"La Vida Boheme - Radio Capital"`, no
accent), further diluted by the trailing song title — `0.59` unfolded. A lower-quality `"(En Vivo)"`
reupload happened to be hosted on the *artist's own* channel (bare `"La Vida Boheme"`, no accent, no
decoration at all) — a clean channel-name match scoring `0.93`, enough to win on artist_relevance
(sorted before `quality` in the tuple) even though `quality` correctly favored the official video
(`4.29` vs `1.20`). This is the tuple-ordering failure shape recurring again (see the "Future
redesign trigger" note above) — but unlike the three prior recurrences, this one had a real,
fixable root cause: the missing-diacritic gap folding already closes for title relevance, just never
extended to artist_relevance. Added as extra `similarity()`/`_text_containment()` branches on the
folded text, on both the candidate-title and channel comparisons — the same "only ever adds a way to
match" pattern `_normalize_multi_artist_punctuation()` already uses in this function.

## `yt-dlp` candidate-pool non-determinism can look like a scoring bug when it isn't

`Koala Voice - Vest` was reported as a wrong pick (a `"(Live)"` upload won) alongside the four real
bugs above, but reproducing it against live yt-dlp output afterward found the scoring already
correct: once the official video is in the candidate pool, it wins decisively (the existing `"live"`
`STRONG_LQ_KEYWORDS` penalty already handles it). The original wrong run most likely just didn't
have the official video in that particular `ytsearch8` call's results at all — see "yt-dlp's
anonymous search is confirmed non-deterministic run-to-run" above. Kept as a regression test anyway
(with the official video *in* the pool) since it costs nothing and guards the scoring behavior
itself, but no code change was needed or made for this one — resist the urge to "fix" a case that
turns out to already work; tune scoring changes to cases that actually reproduce wrong.

A second case from the same investigation turned out the same way: `Easy Life - Pockets` was
reported as finding nothing at all, but the DB title was already the correct `"Pockets"` (plural) by
the time this was checked, and that title resolves correctly against real yt-dlp output with no
code change. The reported run's console output showed a singular `"Pocket"` query, so the DB title
was very likely still wrong (or a typo crept in transcribing the log) at the time of that specific
failure — see "A contaminated DB `title` field defeats matching" above for the general shape of this
class of problem (a DB text field not matching the actual song, not a `score_result()` gap). Kept as
a regression guard for the same reason as Koala Voice above.

## A "ft."/"feat." credit can be split across the song title itself, not adjacent to the other name

`_normalize_multi_artist_punctuation()` (see the comma/slash/ampersand section above) only bridges a
*punctuation* difference between two spellings that still keep every artist name adjacent to each
other. Real uploads for a "ft."/"feat." collab routinely don't: the primary artist appears before
the song title and the featured artist after it, e.g. `"Drenchill - Freed from Desire ft.
Indiiana"` — the DB's own combined field, `"Drenchill ft. Indiiana"`, never appears as one
contiguous phrase in that upload's title at all. Real case: `Drenchill ft. Indiiana - Freed From
Desire` — every real, popular upload spelled the credit differently enough (split across the title,
`"feat."` instead of `"ft."`, comma-separated) to score only `0.44`–`0.69` `artist_relevance` via
every existing containment/similarity check, while a `"(Bass Boosted)"` reupload (228 views) happened
to spell the whole DB phrase `"Drenchill ft. Indiiana"` verbatim and contiguously in its own title,
scoring a clean `1.0` and winning outright — `artist_relevance` sorts before `quality`, which had
already correctly penalized `"Bass Boosted"` (`-1.64` vs. the real uploads' `3.4`–`5.4`). The sixth
recurrence of the tuple-ordering failure shape (see the note above) — but, like the diacritic-folding
gap before it, a genuinely new, fixable root cause rather than another instance of an already-covered
one.

`_multi_artist_tokens()` splits a combined field on any of the common connectors
(`,`/`/`/`&`/`feat.`/`ft.`/`featuring`/`x`/`vs.`, case-insensitive) into individual names, and
`_multi_artist_names_all_present()` checks each one independently — present *anywhere* in the
candidate's title+channel text, not necessarily adjacent or in the DB's own order — added as one
more `max()` branch in `_artist_relevance_for()`. Requiring *every* name present (not just one) means
this only ever adds a way to match a genuine collab upload; a solo track by just one of the named
artists still won't satisfy it. Known remaining gap, not fixed here: a featured-artist credit
written *inside a bracket* (e.g. `"(feat. Indiiana)"`) gets stripped away entirely by
`_relevance_text()`'s bracket-removal before this check ever sees it — one of the real Drenchill
candidates lost this way (stayed at `0.51`), though the overall pick was unaffected since a
different real upload already won cleanly. Only worth revisiting if a future real case actually
needs that specific candidate to win.

## Known regressions this logic exists to prevent

Concrete cases hit during development — useful as a regression checklist if this scoring is ever
simplified:
- `TOR BAND - Бацька [Official Video]` → wrongly matched an unrelated "All inclusive" video
  (title-only + containment now scores this near 0).
- `Vivienne Mort - Душа [Save Ukraine - #StopWar]` → wrongly matched an unrelated video sharing
  only the campaign-tag caption (bracket stripping now excludes that from comparison).
- `Genie - Black Belt` → wrongly matched `"Trick Or Treat"` at relevance 0.38, above the old flat
  0.35 threshold (the scaled per-title threshold now requires 0.5+ for a title this length).
- `Golden Boy With Miss Kittin - Nix` / `Gosia Kunc - As` → correct matches wrongly *rejected*
  because the short exact title was diluted by candidate prefix text (`_title_containment()` now
  rescues these).
- `Cannons - Fire for You` → a `(Sped Up)` edit beat the ordinary version on a relevance tie (now
  deprioritized via `LQ_KEYWORDS`).
- `emade - Jesteś tylko` → the correct video (title "Jesteś tylko", uploaded on the
  "Emade - Topic" channel) wasn't recognized as artist-relevant and wasn't even in the top-5 search
  results (channel-aware artist matching + Topic bonus + widened candidate pool to 8 address this).
- `Foals - 2001` → a `"(Dan Carey Dub) – Official Audio"` remix reupload beat the real official
  video on quality alone (`"official audio"` double-counted as both itself and the separate
  `"audio"` keyword — see above).
- `Dawid Zły - Timczasã` / `Wszëtce Lëdze` → matched the *wrong* Dawid Zły song because both DB
  titles carried the same un-bracketed channel branding (`KaszubskiHipHop.pl`), inflating
  similarity against any candidate from that channel (see above — a data problem, not a scoring
  one).
- `SLAUGHTER TO PREVAIL - Bratva` → an "(Instrumental)" reupload (60K views) beat the real track
  (8.3M views) on an `artist_relevance` tie caused by pure whitespace-formatting noise (see the
  popularity section above).
- `Passive Voice - Тебе пам'ятаю` / `Ten Preston feat. Sitek - 71` → both had zero relevant
  candidates purely because the query included the literal word "audio" (see the query-text
  section above) — no scoring change was needed, only removing it from the query.
- `Gary Clark Jr. - Ain't Messin' 'Round` → the two real uploads each had only *one* of the DB
  title's two apostrophes, scoring relevance ~0.69 against an unrelated but exactly-punctuated
  candidate's 1.0 (see the doubled-letter/apostrophe handling in `_relevance_text()` above).
- `WE BUTTER THE BREAD WITH BUTTER - N!CE` → the real upload's title had extra uploader branding
  ("// Official Music Video // AFM Records") that diluted plain `similarity()`-based
  `artist_relevance` below a reaction video's shorter, undecorated title (see the
  `_text_containment()`-for-artist section above).
- `Zob - Cantec de dragoste` → a cover by a completely different duo (563K views, far more popular
  than any of Zob's own uploads) outscored the real artist's uploads for the same
  `artist_relevance` dilution reason as above.
- `Måneskin - Zitti e buoni` / `Foals - 2001` / `Bloodywood - Ari Ari` / `Błażej Król - Zaklęcie` →
  "eurovision version", "official" (see above), a clean channel-name match, and a large
  pre-existing popularity gap respectively resolved these — see the `VIDEO_KEYWORDS`/`LQ_KEYWORDS`
  rebalancing and `"official"` sections above for the two that needed a real code change.
- `Bastille - Pompei` → rejected outright by the relevance gate over a single missing "i" (see the
  doubled-letter tolerance section above).
- `Daði Freyr - Bitte` / `OBERSCHLESIEN - Król Olch` → the real official video only barely (or
  didn't) beat a live/festival recording due to the old uniform `VIDEO_KEYWORDS`/`LQ_KEYWORDS`
  weights (see the rebalancing section above).
- `Valeria Stoica - Get Back (Lorin Rymbu & Denis Rynda Remix Extended)` → any remix scored
  identical relevance once the DB title's remix-selector bracket was stripped, so a completely
  unrelated remix won (see the bracket-selector-hint section above).
- `ABRADAB - Niesmiertelnosc (Muzyka Daje)` / `bayski - набіраи (acoustic)` → missing Polish
  diacritics and a Cyrillic "и"/"й" typo respectively defeated relevance matching before diacritic
  folding was added (see the typo/spelling-variant section above).
- `Rival Sons - Darkfighter` → a "Track by Track" promo video (content *about* the album, not the
  song) only barely lost to the real official audio — margin 0.29, the same fragile-near-tie shape
  as `Daði Freyr - Bitte` above. `"track by track"` joined `LQ_KEYWORDS` alongside
  "react"/"review"/"teaser" as the same category of signal: content that isn't the song at all.
- `Elvis Crespo - Suavemente` → the real video's own SEO-stuffed tags ("remix", "karaoke",
  "instrumental") tripped `LQ_KEYWORDS` and cost it the HQ bonus too, dropping a 299M-view official
  upload below a 19.6M-view unrelated collab (see the `tags`-scoping section above).
- `Бумбокс - Нездара` / `Плач Єремії - Вона` / `Hall & Oates - Maneater` / `Junecapone -
  Depravity` → the real channel uses a genuinely different name for the artist than the DB
  (`familyboombox`, Taras Chubai, "Daryl Hall & John Oates", "June" respectively) — no fuzzy
  string match can bridge a real name change; fixed via the `synonyms` column (see above).
- `Al Di Meola - Double Concerto` → the real Topic-channel upload's `channel` field carries no
  "-Topic" suffix at all, so a live Budapest recording won on view count alone; fixed via the
  `track` official-release signal (see above).
- `SunSay - В твоих глазах сияю я` / `SunSay - Немовля` → the real channel handle
  (`"sunsaymusic"`) concatenates the artist name with no separator, defeating whole-word
  containment; fixed via the channel-handle-prefix match (see above). The second case also needed
  the `track` signal to pick between two candidates sharing that same channel.
- `Waglewski, Fisz, Emade - Bóg` → comma- vs. slash-separated multi-artist punctuation dropped the
  real Topic channel's `artist_relevance` below an unrelated live reupload's, a further recurrence
  of the tuple-ordering failure shape; fixed via `_normalize_multi_artist_punctuation()` (see
  above).
- `Taco Hemingway - Fuck Your List` → the real video is age-restricted and excluded from every
  search backend this app uses (verified, not theorized — see above); a lyrics-translation video
  that only *mentioned* the song used to win by default. Fixed two ways: `NOT_THE_SONG_KEYWORDS`
  rejects the translation video outright, `MIN_ARTIST_RELEVANCE` rejects an unrelated
  coincidentally-similar-titled song by a different artist — and `Song.youtube_video_id` is the
  actual way this one specific song now resolves correctly, since no search-side fix can find a
  video the search backend itself excludes.
- `Вольны хор - Пагоня` → confirmed the official-release signal correctly prefers a genuine
  Topic-channel release (full `track`/`artists`/`album` metadata, more views) over a legitimate but
  non-canonical reupload once both clear the relevance bar.
- `Akute - Cicha, jak maja śmierć` / `Akute - Ihołki` → the real official upload (Cyrillic title,
  `akutemusic` channel, `track` metadata set) scored too low on relevance against the Lacinka DB
  title to be considered at all, leaving only live bootleg reuploads (Latin-titled, so they matched
  fine) to win by default; fixed via the alternate-script retry (see `transliteration.py` above) —
  confirmed end-to-end for the first song, confirmed the retry engages correctly for the second but
  landed back on the live pick that specific run because yt-dlp's anonymous search returned zero
  results for the transliterated query at that moment (see that section's known limitation).
- `Benjamin Clementine - Cornerstone` → a BBC "Later... with Jools Holland" TV performance beat the
  real official video on a narrow quality margin because the show's own name carries no "live"
  wording at all (see the Jools Holland section above).
- `Gaba Kulka - Biegnij dalej sam` → the DB credited the song solely to Gaba Kulka (a guest
  vocalist), not the real credited act `Fisz Emade Tworzywo`, so no real upload's title/channel
  ever matched — a data-attribution problem, not a scoring one (see the section above).
- `Betoko - Breaking (Original Mix)` → an officially-released "(Club Edit)" beat the real Original
  Mix purely from an unconditional official-release bonus that didn't check for an alternate-version
  keyword hit first (see the official-release-bonus-gating section above).
- `Genesis - Firth Of Fifth` → a named live concert film ("When in Rome 2007") with no "live"
  wording nearly won on view count alone (see the Jools Holland section above).
- `Krzysztof Zalewski - Milosc Milosc` → Polish "ł" doesn't NFKD-decompose, so an ASCII-typed DB
  title never exact-matched any candidate, including the real ones (see the "ł" section above).
- `La Vida Bohème - Radio Capital` → `artist_relevance` never folded diacritics, so a label-hosted
  official video's accent-mismatched channel/title match lost to a live reupload hosted on the
  artist's own (also accent-mismatched, but undecorated) channel (see the section above).
- `Koala Voice - Vest` → reported as wrong but not actually a scoring bug — reproducing it found the
  scoring already correct once the official video is in the candidate pool; the original run's
  candidate pool likely just didn't include it (see the non-determinism section above).
- `Easy Life - Pockets` → also not a scoring bug — the DB title was already the correct plural
  "Pockets" by the time this was checked, and that resolves correctly (see the section above).
- `Drenchill ft. Indiiana - Freed From Desire` → every real upload split or reworded the "ft."
  credit enough to lose artist_relevance to a "(Bass Boosted)" reupload that happened to spell the
  DB's combined field verbatim (see the "ft."/"feat." section above).

**Not covered by any of the above:** `Vinsent - Praciahvaju Żyć` — same shape as the Akute cases
above (Latin DB title, Cyrillic-only real upload), not specifically re-tested since the mechanism
was built. `Stray Kids - 특(S-Class)` (Hangul, not Cyrillic) stays fully open — see
`transliteration.py`'s section above for why.
