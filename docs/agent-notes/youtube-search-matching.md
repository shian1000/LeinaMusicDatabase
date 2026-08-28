# YouTube search matching (`src/utils/youtube/manage_youtube_playlists.py`)

`score_result()` picks the "best match" video for a DB song, searching via yt-dlp first (free)
then falling back to the YouTube Data API. Several earlier, simpler versions of this function
caused real wrong-song matches — this file documents why the current logic looks the way it does,
so it doesn't regress if simplified later.

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
(`discoveries_manager.py`, as an album-lookup fallback name) and never populated by any current
code path. It's a plain string with no enforced format; comma-separated is now the convention for
multiple aliases (`_parse_synonyms()`).

`score_result()` takes an optional `artist_synonyms: str | None` and checks `artist_relevance`
against the primary DB artist name *and* every parsed synonym, taking the max
(`_artist_relevance_for()`, called once per name). This flows from `create_yt_playlist()` reading
`entry.get("synonyms")` from the cache, which `init_cache()` (`yt_cache.py`) now populates from
`song.artist.synonyms` — i.e. the fix only takes effect once the DB row's `synonyms` field is
actually filled in for that artist; the mechanism alone doesn't do anything for an artist with no
synonyms recorded. Verified all four cases jump to `artist_relevance == 1.0` once the synonym is
supplied (previously `0.0`–`0.69` depending on how much accidental character overlap there was).

**Not fixed by this**: `Stray Kids - 특(S-Class)` stays open — that's a *title*-formatting mismatch
(Hangul mixed with a Latin parenthetical, not diagnosed in detail yet), not an artist-name problem,
so the synonym mechanism doesn't apply to it. Should resolve to
[youtu.be/JsOOis4bBFg](https://youtu.be/JsOOis4bBFg) once someone gets to it.

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
