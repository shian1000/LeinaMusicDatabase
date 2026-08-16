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

**Future redesign trigger:** if more cases like this keep surfacing — an official-channel alternate
version (live/session/orchestral/demo) beating the plain studio upload because `artist_relevance`
outranks `quality` in the sort tuple — that's a signal the tuple priority itself needs
reconsidering (e.g. folding a strong `LQ_KEYWORDS` hit into the relevance/gating stage instead of
only `quality`), not another `LQ_KEYWORDS` tweak. One-off keyword additions are the right fix for a
single missing signal (as `demo`/`dub`/`orchestra` were here); a *pattern* of the same tiebreak
losing is a design problem, not a vocabulary gap.

Regression tests: [tests/test_youtube_search.py](../../tests/test_youtube_search.py) —
`test_score_result_does_not_double_count_official_audio_keyword` asserts the exact `quality` value
for the Dan Carey Dub title, and `test_search_video_ytdlp_does_not_pick_the_remix_for_foals_2001`
replays the real yt-dlp candidate list (mocked `subprocess.run`) and asserts neither remix wins.

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
