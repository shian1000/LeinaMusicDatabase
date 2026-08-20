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

It is **not** a reliable proxy for "not a live/alternate version", though — confirmed with
`OBERSCHLESIEN - Król Olch`: a Woodstock festival recording has *more* views (5.1M) than the actual
studio video (1.4M), so it still wins even after being correctly tag-flagged as live (see next
section) and penalized `-1`. A single categorical LQ penalty isn't always enough to outweigh a real
popularity gap, and cranking that one constant up to force this specific case to flip was
deliberately **not done** — see the "Future redesign trigger" note above; this is the same
structural issue recurring in a second form (popularity vs. keyword-penalty magnitude this time, not
`artist_relevance` vs. `quality`). Left as a known, open limitation rather than force-fit — do not
"fix" this by arbitrarily inflating `"live"`'s weight without addressing the general pattern.

Also left open, unrelated to popularity: `NIZKIZ - Правілы` picked a 67-view excerpt over the
74K-view real upload because the real upload spells "Правілы" the Russian way (missing the
Belarusian "і"), scoring relevance 0.55 vs. the excerpt's exact-match 1.0 — relevance is compared
*before* quality/popularity in the sort tuple, so popularity is never even consulted for this one.
A spelling-variant/orthography problem, not a popularity or quality-weighting problem.

## Keyword scanning now covers `tags` too, not just the title (yt-dlp only, free)

A live recording's *title* often carries no signal at all —
`"Oberschlesien - Król Olch #Woodstock2016"` has no English "live"/"concert" word — but yt-dlp's
free `tags` field did: `"Oberschlesien Król Olch na żywo"` ("na żywo" is Polish for "live"), plus
repeated festival names, while the actual studio upload's tags were clean (verified both, no
false-positive risk observed). `score_result()` now takes an optional `tags: list[str] | None` and
builds `keyword_text = title + " " + " ".join(tags)` for the `HQ_KEYWORDS`/`LQ_KEYWORDS`/
`VIDEO_KEYWORDS`/`HIGH_TRUST_KEYWORDS` scan — relevance/artist_relevance still use the title alone,
unaffected. Deliberately `tags` only, not `description`: tags are curated keywords an uploader
picked, description is freeform prose where "live" could appear in unrelated boilerplate (tour
dates, etc.) and false-positive. `LQ_KEYWORDS` gained `"na żywo"` for this.

`HIGH_TRUST_KEYWORDS`/`HIGH_TRUST_BONUS` (currently just `"oficjalny odsłuch albumu"` — Polish for
"official album listen", a label's official full-album premiere upload) is a separate list from
`HQ_KEYWORDS`, scored at `+4` instead of the generic `+2`, for phrases specific enough to be an
unambiguous "this is the real official upload" signal rather than a generic quality indicator like
"HD"/"remaster". Keep it that way if extending it — don't fold new entries into `HQ_KEYWORDS` just
because they're both "good" signals; the point of a separate list is the stronger, deliberately
uneven weight.

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
