import logging
import math
import os
import re
import shutil
import subprocess
import unicodedata
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Dict

import questionary
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from utils.youtube.yt_cache import make_song_key, save_cache
from utils.youtube.yt_cache import load_cache, init_cache, clear_cache
from utils.youtube.yt_cache import save_cache
from utils.youtube.yt_cache import make_song_key
from utils.common.text_utils import remove_brackets, similarity, scaled_similarity_threshold
from utils.common.normalizer import APOSTROPHES
from utils.youtube.transliteration import is_transliterable, transliterate
from utils.database.database_sessions import submit_global_database_session
import json


import json
from googleapiclient.errors import HttpError


SCOPES = ["https://www.googleapis.com/auth/youtube"]

TOKEN_PATH = ".secrets/token.json"
CLIENT_SECRET_PATH = ".secrets/client_secret.json"

# Manual `Song.youtube_video_id` annotation meaning "a human has confirmed
# this song has no video on YouTube at all" — distinct from the field being
# empty/unset (never checked) and from a real video_id (found, possibly
# stale), as opposed to the existing "excluded from search results" escape
# hatch (see docs/agent-notes/youtube-search-matching.md), where a real
# video_id does exist and is stored instead. For now this is a data-only
# record with no dedicated runtime behavior yet: create_yt_playlist()
# normalizes it to "no stored link" and searches normally, same as an empty
# field — there's no mechanism yet to have it actually skip the search.
NO_VIDEO_SENTINEL = "N/A"

# Dedicated, always-on log file for the "reuse/validate/persist
# Song.youtube_video_id" flow in create_yt_playlist() — separate from the
# app-wide debug.log (utils.common.debug), which is silent unless a `.debug`
# file is present, so a stale/invalid stored link or a failed DB write is
# still traceable after the fact without the user needing to have debug mode
# on ahead of time.
_LOG_PATH = Path(__file__).resolve().parents[3] / "youtube_link_cache.log"
_logger = logging.getLogger("youtube_link_cache")
if not _logger.handlers:
    _handler = logging.FileHandler(_LOG_PATH, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False

HQ_KEYWORDS = ["hq", "hd", "high quality", "official audio", "audio", "remaster", "flac", "320", "official"]
# "official" is a genuine trust signal on its own, not just as part of
# "official audio" — being confirmed official content and being in the
# less-preferred video format are two separate axes, but previously only
# the format axis (VIDEO_KEYWORDS' penalty) was tracked, so a video
# correctly labeled "[Official Music Video]" scored *worse* than a random,
# unlabeled fan upload that said nothing distinguishing at all. Real case:
# Foals - 2001, a bare-number title where every candidate ties at 1.0
# relevance/artist_relevance (so quality alone decides) — the real official
# video used to score -1 (video penalty, no offsetting signal) while a
# random lyric video and an unlabeled "'2001'" upload both coasted to 0
# purely by not saying anything. "official" already dedupes against
# "official audio"/"official mv" via _non_overlapping_hits (a hit wholly
# contained in another hit from the same list doesn't double-count), so
# this doesn't change scoring for anything already covered by those.
# Keywords that suggest we should deprioritize (lower = worse). "track by
# track" (a band explaining/promoting an album track, not the track itself)
# joins the same category as "react"/"review"/"teaser" — content *about* the
# song, not the song. Real case: Rival Sons - Darkfighter, where a "Track by
# Track" promo video only barely lost to the real official audio (margin
# 0.29) on view count alone before this — the same fragile-near-tie shape
# fixed for Daði Freyr, but for "isn't music at all" rather than "isn't the
# canonical arrangement". "club edit" joins for the same reason as "remix" —
# a genuinely different mix of the track, not the plain one. Real case:
# Betoko - Breaking (Original Mix) — a "(Club Edit)" upload, distributed to
# a Topic channel with its own `track` metadata, tied 1.0/1.0
# relevance/artist_relevance with the real "(OKO Recordings)" upload (an
# artist-channel upload with no official-release metadata at all) and won
# purely on the unconditional official-release quality bonus — see that
# bonus's own gating fix below for why this keyword alone wasn't enough.
# "reaction" joins "react" as its own explicit entry (rather than relying on
# "react" substring-matching it) now that keyword matching requires a whole
# word — see _keyword_present()'s docstring for why plain substring matching
# was retired.
LQ_KEYWORDS = ["concert", "tour", "performance", "session", "acoustic", "cover", "karaoke", "instrumental", "remix", "club edit", "sped up", "slowed", "nightcore", "8d audio", "bass boosted", "demo", "dub", "orchestra", "orchestral", "react", "reaction", "review", "teaser", "eurovision version", "high tone", "track by track"]
# Titles matching these are content *explaining/analyzing* the song, not a
# recording of it at all — unlike LQ_KEYWORDS above (still real recordings
# of the song, just a different arrangement/quality), these fully
# disqualify a candidate (relevance forced to 0, see score_result()) rather
# than just costing it a quality tiebreak. That distinction matters
# structurally: `quality` only ever breaks a tie between candidates already
# sharing the top relevance score — it can never rescue a search where the
# *only* candidate at 1.0 relevance is the wrong kind of content. Real case:
# Taco Hemingway - "Fuck Your List" is age-restricted and invisible to
# anonymous search entirely (verified against every yt-dlp `player_client`
# option — see search_video_ytdlp()'s docstring), so a "Tłumaczenie Taco
# Hemingway - Fuck Your List | LyricsTranslationTV" video (a lyrics
# translation, not the song) was the *only* candidate whose title
# title-contained the exact expected title — no quality penalty, however
# large, could have stopped it from winning, since nothing else tied its
# relevance for `quality` to even be consulted against.
NOT_THE_SONG_KEYWORDS = ["tłumaczenie", "lyrics translation"]
# "live"/"na żywo" get a bigger penalty than the rest of LQ_KEYWORDS: a live
# recording is essentially never the plain studio version (unlike, say, an
# "acoustic" cut, which occasionally *is* the canonical release), so it
# deserves to reliably outweigh a moderate popularity edge, not just barely
# tip the balance. Real case: Daði Freyr - Bitte's official video (235K
# views) only barely beat a live recording (23K views) at the old uniform
# -1 weight — a coincidence away from picking the live version instead.
# "woodstock" is grouped in here too — Poland's Pol'and'Rock/Woodstock
# festival is a live-performance event, and its name shows up in tags/titles
# in many forms ("Woodstock", "#Woodstock2016", "woodstock") that a plain
# lowercased substring check already catches without listing each variant.
# "jools holland" is the same shape of gap as "woodstock": a specific,
# unambiguous live-performance venue whose own name carries no English
# "live"/"concert" word at all. Real case: Benjamin Clementine - Cornerstone
# — the real official video (7Dc5BQ31iLw, 6.7M views) lost to a BBC "Later...
# with Jools Holland" clip (CJJNl1p-PGA, 1.2M views, quality 5.06 vs 4.83)
# purely because the official video's own "(Official Video)" tag cost it the
# VIDEO_PENALTY while the Jools Holland clip incurred no LQ penalty at all
# and even picked up an HQ bonus from "HD" (BBC Two HD).
# "when in rome" is the same shape again — Genesis' "When in Rome 2007" is a
# specific, named live concert film/DVD whose own title carries no "live"
# wording. Real case: Genesis - Firth Of Fifth — a "..., I Know What I Like
# (When in Rome 2007)" reupload (11.3M views) had no LQ penalty at all and
# nearly outscored the real official audio purely on view count; the
# official audio still won that particular run, but only because it happened
# to be in the fetched candidate pool — a less popular or absent official
# candidate would have lost to this the same way Jools Holland did.
STRONG_LQ_KEYWORDS = ["live", "na żywo", "woodstock", "jools holland", "when in rome"]
STRONG_LQ_PENALTY = 3
VIDEO_KEYWORDS = ["official video", "music video", "mv", "official mv", "video clip"]
# Lowered from -2: being a video instead of an audio-only upload isn't
# itself evidence of being the *wrong* content, just a format preference —
# but at -2 it was heavily enough weighted to make the correct, far more
# popular official video lose to an obscure alternate cut just for being a
# video (e.g. Måneskin's 230M-view official video losing to an 8.9M-view
# "Eurovision Version" reupload that triggered no penalty at all).
VIDEO_PENALTY = 1
# Phrases that are an especially strong, unambiguous "this is the real,
# official, unedited upload" signal — trusted with a bigger bonus than the
# generic HQ_KEYWORDS list. "oficjalny odsłuch albumu" is Polish for
# "official album listen" (a label's official full-album premiere upload).
HIGH_TRUST_KEYWORDS = ["oficjalny odsłuch albumu"]
HIGH_TRUST_BONUS = 4

# Generic descriptor words that don't identify a *specific* version on their
# own — used to filter noise out of a DB title's bracket content before
# treating it as a "which exact version" selector (see _selector_tokens()).
# Built from the keyword lists above plus common remix/version vocabulary,
# so a bracket that's just ordinary branding ("Official Video", "HD") never
# gets treated as a meaningful selector — only bracket content with real,
# specific identifying words left over (a named remixer, a collaborator)
# does.
_SELECTOR_STOPWORDS = {
    word
    for phrase in HQ_KEYWORDS + LQ_KEYWORDS + STRONG_LQ_KEYWORDS + VIDEO_KEYWORDS + HIGH_TRUST_KEYWORDS
    for word in phrase.split()
} | {"remix", "mix", "edit", "extended", "radio", "club", "vip", "feat", "ft", "featuring", "prod", "the", "and", "of", "with", "by"}
SELECTOR_MATCH_BONUS = 3

# Baseline minimum title relevance a candidate must have to be accepted at
# all (see _min_relevance_for — short titles require a much higher bar than
# this). Without this, two candidates that both score 0 on the keyword lists
# above are indistinguishable, so a completely unrelated video could "win"
# just by being returned first. Gating on title (rather than artist+title
# combined) matters: a wrong song by a same-named artist, or a wrong video
# that merely shares a caption like "[Save Ukraine - #StopWar]" with the
# requested title, can still look deceptively similar once the artist name
# or shared junk text is folded into one comparison string.
MIN_RELEVANCE = 0.5

# Minimum artist_relevance a candidate must clear to be picked at all, on
# top of (never instead of) the title relevance gate above. The title-only
# gate (see score_result()'s docstring for why relevance is title-only) has
# a blind spot: a completely unrelated song by a completely unrelated
# artist can still pass it on title alone, by coincidence. Real case: Taco
# Hemingway - "Fuck Your List" is age-restricted and invisible to anonymous
# search entirely (see search_video_ytdlp()'s docstring). Once the one
# candidate that did title-match ("Tłumaczenie ... | LyricsTranslationTV",
# a translation video, not the song — see NOT_THE_SONG_KEYWORDS) is
# disqualified, the next-best title match was "Fuck ya list" by "Paccman
# chico" — an entirely unrelated artist, relevance 0.85 from coincidental
# character overlap with "Fuck Your List", but artist_relevance only 0.29.
# Rejecting that (reporting no match, which is what happens when the
# genuine video is unreachable) is safer than confidently adding a wrong
# song. 0.35 sits well above that 0.29 and well below every legitimate
# candidate's artist_relevance across the existing regression suite (a real
# title/channel match — even a heavily decorated one, or one relying on
# containment rather than a raw ratio — clears at least ~0.5 in every case
# checked).
MIN_ARTIST_RELEVANCE = 0.35

# Unicode ranges for scripts written without spaces between words/characters
# (hiragana/katakana, CJK ideographs, hangul syllables). Titles in these
# scripts need substring-containment matching instead of whole-word regex
# matching, since there's no word boundary to anchor on.
_NO_SPACES_SCRIPT = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def _keyword_present(keyword: str, text: str) -> bool:
    """Whether `keyword` appears in `text` as a whole word/phrase, not merely
    as a substring of a longer, unrelated word. Used for every keyword list
    in this file (HQ/LQ/STRONG_LQ/VIDEO/HIGH_TRUST/NOT_THE_SONG) — plain `kw
    in text` was the check here until a real case exposed it as unsafe: `Al
    Di Meola - Double Concerto`'s real Topic-channel upload (bare title
    "Double Concerto") silently took LQ_KEYWORDS' "concert" entry as a hit,
    because "concert" is a literal substring of "Concerto". This had always
    been true, but stayed harmless as long as the official-release quality
    bonus applied unconditionally (see that bonus's own docstring) — gating
    the bonus on "no LQ hit at all" (to fix a different real case, Betoko -
    Breaking) suddenly made this latent false positive decisive, dropping
    the real upload's quality enough to lose to an unrelated live recording.
    Every existing keyword-list entry is a real word or phrase on its own
    (never intended to match as a fragment of some other word), so this is a
    strictly more correct check everywhere it's used, not a narrower one for
    just this case — the existing dedup in `_non_overlapping_hits()` (e.g.
    "audio" inside "official audio") still works unchanged, since those are
    cases of one *whole word* being a phrase's own substring, which a word
    boundary still finds on both sides of the space.
    """
    return re.search(r"\b" + re.escape(keyword) + r"\b", text) is not None



# ---------------------------------------------------------
# AUTHENTICATION
# ---------------------------------------------------------

def get_youtube_service():
    """Authenticate and return a YouTube API service instance."""

    creds = None

    # Load cached credentials
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    # If no valid credentials, login with OAuth
    if not creds or not creds.valid:
        try:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise Exception("No valid refresh token")
        except Exception:
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_PATH, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())


        # Save token for next time
        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


# ---------------------------------------------------------
# DOWNLOADING VIDEOS
# ---------------------------------------------------------

def download_youtube_video(url: str, output_dir: str | None = None) -> str | None:
    """Download a video from a YouTube link using yt-dlp."""
    link = (url or "").strip()
    if not link:
        print("  ✖ No link provided")
        return None

    target_dir = output_dir or str(Path(__file__).resolve().parents[3] / "import")
    Path(target_dir).mkdir(parents=True, exist_ok=True)

    yt_dlp_executable = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if not yt_dlp_executable:
        venv_bin = Path(sys.prefix) / "bin"
        candidate = venv_bin / "yt-dlp"
        if candidate.exists():
            yt_dlp_executable = str(candidate)

    if not yt_dlp_executable:
        try:
            import yt_dlp  # type: ignore
        except ModuleNotFoundError:
            print("  ✖ yt-dlp is not installed or not available on PATH")
            return None
        yt_dlp_executable = sys.executable

    output_template = str(Path(target_dir) / "%(title)s.%(ext)s")
    command = [
        yt_dlp_executable,
        "--no-playlist",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "-o",
        output_template,
        "--print",
        "after_move:filepath",
        link,
    ]

    if yt_dlp_executable == sys.executable:
        command = [yt_dlp_executable, "-m", "yt_dlp", *command[1:]]

    print(f"\nDownloading from: {link}")
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except FileNotFoundError:
        print("  ✖ yt-dlp is not installed or not available on PATH")
        return None
    except subprocess.TimeoutExpired:
        print("  ✖ Download timed out")
        return None

    if result.returncode != 0:
        error_output = (result.stderr or result.stdout).strip()
        if error_output:
            print(f"  ✖ Download failed: {error_output}")
        else:
            print("  ✖ Download failed")
        return None

    downloaded_path = None
    for line in result.stdout.splitlines():
        candidate = line.strip()
        if candidate:
            downloaded_path = candidate

    if downloaded_path:
        print(f"  ✔ Downloaded: {downloaded_path}")
    else:
        print("  ✔ Download completed")

    return downloaded_path


# ---------------------------------------------------------
# PLAYLIST CREATION
# ---------------------------------------------------------

def create_playlist(youtube, title: str, description: str = "") -> str:
    """Create a YouTube playlist and return its ID."""

    request_body = {
        "snippet": {
            "title": title,
            "description": description
        },
        "status": {"privacyStatus": "private"}
    }

    response = youtube.playlists().insert(
        part="snippet,status",
        body=request_body
    ).execute()

    playlist_id = response["id"]
    print(f"Created playlist with ID: {playlist_id}")
    return playlist_id


# ---------------------------------------------------------
# LISTING PLAYLISTS
# ---------------------------------------------------------

def get_playlists_in_creation_order(youtube) -> List[Dict]:
    """Fetch all of the authenticated user's playlists, newest created first.

    The Data API v3 playlists().list endpoint has no sort/order parameter --
    it returns items in whatever order YouTube's backend has them in, which
    in practice tracks last-modified rather than creation date. Each item's
    snippet.publishedAt is the actual creation timestamp, so we page through
    every playlist and sort client-side to get true creation order.
    """
    playlists: List[Dict] = []
    page_token = None

    while True:
        response = youtube.playlists().list(
            part="snippet,contentDetails",
            mine=True,
            maxResults=50,
            pageToken=page_token
        ).execute()

        playlists.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    playlists.sort(key=lambda p: p["snippet"]["publishedAt"], reverse=True)
    return playlists


def show_playlists_in_creation_order():
    """Print all of the user's YouTube playlists, newest created first."""
    from utils.ui.display_utils import display_playlists

    youtube = get_youtube_service()

    try:
        playlists = get_playlists_in_creation_order(youtube)
    except HttpError as e:
        print(f"  ✖ Failed to fetch playlists: {e}")
        return

    if not playlists:
        print("No playlists found.")
        return

    display_playlists(playlists)


# ---------------------------------------------------------
# SEARCHING VIDEOS
# ---------------------------------------------------------

def search_video_cached(youtube, cache: dict, artist: str, title: str) -> str | None:
    key = make_song_key(artist, title)
    entry = cache["songs"][key]

    # Already searched
    if entry["video_id"] is not None:
        return entry["video_id"]

    print(f"\nSearching for: {artist} - {title}")

    try:
        response = youtube.search().list(
            part="snippet",
            q=f"{artist} - {title}",
            type="video",
            maxResults=1,
            safeSearch="none"
        ).execute()

    except HttpError as e:
        if e.resp.status == 403 and b"quotaExceeded" in e.content:
            print("🛑 Quota exceeded — progress saved.")
            save_cache(cache)
            raise SystemExit(1)
        raise

    items = response.get("items", [])
    video_id = items[0]["id"]["videoId"] if items else None

    entry["video_id"] = video_id
    save_cache(cache)

    if video_id:
        print(f"  ✔ Found video: {video_id}")
    else:
        print("  ✖ No results found")

    return video_id

def _bracket_selector_hints(title: str) -> list[str]:
    """Extract bracket-wrapped text from a DB title before _relevance_text()
    discards it for good.

    Bracketed text is stripped for the main relevance comparison because
    it's *usually* disposable annotation ("[Official Video]", "[WIDEO]") —
    but sometimes it's the opposite: the specific version being asked for.
    Real case: DB title "Get Back (Lorin Rymbu & Denis Rynda Remix
    Extended)" — stripping the bracket for relevance meant *any* remix of
    "Get Back" scored identical 1.0 relevance, so a completely different
    remix won. Rather than guessing up front whether a given bracket is
    junk or a meaningful selector (the two look identical syntactically),
    keep what's stripped and check it separately in score_result() against
    the *candidate's* own (unstripped) title/tags — see _selector_tokens().
    """
    return [m.strip() for m in re.findall(r"[\(\[]([^)\]]*)[)\]]", title) if m.strip()]


def _selector_tokens(hint: str) -> set[str]:
    """Reduce a bracket hint to its specific, identifying words.

    Generic branding words ("Official", "Video", "Remix", "Extended", ...)
    are filtered via _SELECTOR_STOPWORDS, so a plain "[Official Video]"
    bracket reduces to an empty set (never treated as a meaningful selector
    to match against), while "Lorin Rymbu & Denis Rynda Remix Extended"
    reduces to the actual identifying names: {"lorin", "rymbu", "denis",
    "rynda"}.
    """
    words = re.findall(r"\w+", hint.lower())
    return {w for w in words if w not in _SELECTOR_STOPWORDS and len(w) > 2}


def _relevance_text(text: str) -> str:
    """Strip bracketed annotations, hashtags, and apostrophe variance before
    comparing titles/artists.

    DB titles/video titles often carry junk like "[Official Video]" or
    "[Save Ukraine - #StopWar]" that isn't part of the song's identity. If
    two unrelated videos both happen to carry the same bracketed tag, a
    plain similarity check on the raw strings would rate them as similar
    for the wrong reason, so that text is dropped before comparing.

    Apostrophes are stripped entirely (not just normalized to one style) —
    real case: DB title "Ain't Messin' 'Round" against a candidate titled
    "Ain't Messin 'Round" (missing the first apostrophe) scored relevance
    0.69, not 1.0, because that single missing character breaks both the
    whole-word containment match and depresses the similarity ratio — enough
    for a much worse, but exactly-punctuated, candidate to outrank it before
    quality is ever consulted. Reuses `normalizer.APOSTROPHES`, the same
    apostrophe-variant set already trusted elsewhere in the app for exactly
    this kind of fuzzy match (`rule_apostrophe_and_common_word_diff`).
    """
    text = remove_brackets(text)
    text = re.sub(r"#\w+", "", text)
    text = re.sub(f"[{re.escape(APOSTROPHES)}]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _collapse_repeated_letters(text: str) -> str:
    """Collapse runs of 2+ identical letters to a single one.

    Tolerates the common doubled-letter typo/spelling-variant pattern (DB
    title "Pompei" vs the real "Pompeii") as one more relevance signal,
    without loosening the relevance bar itself for short titles in general —
    this is applied equally to both sides of the comparison, so it only
    helps when a title is otherwise an exact match modulo a repeated letter,
    not a blanket relaxation of what counts as similar. Real case: "Bastille
    - Pompei" scored 0.50 relevance against the real "Pompeii" (needed 0.85
    for a title this short — see _min_relevance_for) purely from that single
    missing letter; collapsed, both become "Pompei" and match exactly. This
    matters more for short titles specifically because a single-character
    difference is a much larger fraction of a short string's similarity()
    ratio than of a long one's — the same scaled threshold that protects
    short titles from false positives also makes them brittle to this exact
    kind of minor, legitimate spelling variance.
    """
    return re.sub(r"(.)\1+", r"\1", text)


# Letters that don't NFKD-decompose (see _fold_diacritics()'s docstring for
# why NFKD alone can't fold these) but have one unambiguous plain-Latin
# equivalent worth substituting directly.
_NON_DECOMPOSING_LETTER_FOLDS = str.maketrans({"ł": "l", "Ł": "L"})


def _fold_diacritics(text: str) -> str:
    """Strip accents/diacritics via Unicode NFKD decomposition + dropping
    combining marks — the same technique `normalizer.normalize()` already
    uses elsewhere in the app, reused here as its own relevance signal
    rather than pulling in that function's other transformations (full
    punctuation removal, lowercasing done elsewhere already) that aren't
    wanted at this stage.

    Real case: DB title "Niesmiertelnosc" (ASCII, missing the accents) vs
    the real "Nieśmiertelność" — exact containment can't match across a
    missing "ś"/"ć", and the fallback similarity() ratio, further diluted
    by an "ABRADAB - " prefix on the candidate side, dropped low enough
    (0.6) to lose to an unrelated "(Live)" recording (0.8). Folded, both
    become "niesmiertelnosc" and match exactly.

    This isn't limited to Latin accents — NFKD decomposition also covers
    some same-script letter variants that read as "the same base letter
    plus a mark" under Unicode, even when they don't look like an accent to
    an English speaker. Real case: DB title "...набіраи" (a typo, Cyrillic
    "и") vs the real "...набірай" (Cyrillic "й", short I) — "й" decomposes
    to "и" + a combining breve, so folding makes the typo and the correct
    spelling identical too, without a Cyrillic-specific rule.

    Not every "looks like a diacritic" letter decomposes this way, though —
    Polish "ł" (U+0142) has no NFKD decomposition at all; it's a genuinely
    separate base letter in Unicode, the same non-decomposing shape already
    documented for Belarusian "і" (see `NIZKIZ - Правілы` above) — but unlike
    "і", an ASCII-typed DB title dropping it almost always means plain "l"
    (there's no ambiguity the way there can be across scripts), so a direct
    substitution table closes this specific gap safely. Real case:
    `Krzysztof Zalewski - Milosc Milosc` (DB, ASCII) vs the real "Miłość
    Miłość" — NFKD folds "ość"'s "ś" to "s" but leaves "ł" untouched, so the
    folded candidate stayed "Miłosc" (still containing "ł"), missing an exact
    match against the DB's plain "Milosc" — this bug then compounded with
    every decorated candidate's own artist-name prefix diluting the
    similarity() fallback, so only the shortest, bare-titled candidate (a
    "(Live)" upload happening to have no artist prefix at all) got close
    enough to clear the relevance bar.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in decomposed if not unicodedata.combining(c))
    return folded.translate(_NON_DECOMPOSING_LETTER_FOLDS)


def _text_containment(expected: str, candidate: str) -> float:
    """How much of `expected` shows up intact inside `candidate`.

    A plain whole-string similarity() ratio unfairly penalizes a short,
    exact title buried in a longer, decorated candidate title (e.g. title
    "As" vs candidate "Gosia Kunc - As" scores only ~0.24 on similarity()
    alone, despite being an exact match) — the surrounding text dilutes the
    ratio. This checks containment directly instead. Used for both title and
    artist relevance — the same dilution problem hits artist matching too
    (e.g. a real official upload titled "... // Official Music Video // AFM
    Records" scored lower artist_relevance than a short, undecorated reaction
    video's title, purely from the extra branding text diluting the ratio).

    For space-delimited scripts, containment only counts if `expected`
    appears as a whole word/phrase, not merely as a substring of a longer
    word — otherwise a short title like "As" would spuriously "contain-match"
    inside an unrelated candidate like "Asian Kungfu Generation - ...".
    Scripts written without spaces (CJK/hangul) have no such word boundary to
    anchor on, so a contiguous-run match is used instead; that's safe there
    because each character carries far more information than a Latin one, so
    short runs aren't the coincidental-substring risk they are in English.
    """
    if not expected:
        return 0.0
    if _NO_SPACES_SCRIPT.search(expected):
        match = SequenceMatcher(None, expected.lower(), candidate.lower()).find_longest_match(
            0, len(expected), 0, len(candidate)
        )
        return match.size / len(expected)
    pattern = r"\b" + re.escape(expected.lower()) + r"\b"
    return 1.0 if re.search(pattern, candidate.lower()) else 0.0


def _parse_synonyms(synonyms: str | None) -> list[str]:
    """Split the artists table's `synonyms` column (a plain comma-separated
    string, no other convention exists in the DB) into individual names.

    Exists because no string-similarity technique can bridge a real name
    change — a fan channel using a translated/English name for a
    non-Latin-script artist, a full legal name vs. a shortened stage name,
    an artist fronting a band under their own name. Real cases: `Бумбокс`'s
    real channel is `familyboombox` (zero character overlap); `Hall & Oates`'
    real channel is `Daryl Hall & John Oates`; `Junecapone`'s real (Topic)
    channel is just `June`. See docs/agent-notes/youtube-search-matching.md.
    """
    if not synonyms:
        return []
    return [s.strip() for s in synonyms.split(",") if s.strip()]


def _normalize_multi_artist_punctuation(text: str) -> str:
    """Treat comma/slash/ampersand as interchangeable multi-artist separators.

    A DB artist field for a collaboration and a candidate's own title/channel
    name often list the same artists with different punctuation conventions
    — DB `"Waglewski, Fisz, Emade"` (comma-separated) vs. the real Topic
    channel's display name `"Waglewski / Fisz / Emade - Topic"`
    (slash-separated). Real case: this punctuation-only difference dropped
    `artist_relevance` to 0.741 for the genuine Topic-channel upload while an
    unrelated "(live Frytka Off)" reupload — whose title happens to spell the
    names with plain spaces, no punctuation at all — scored 0.833 purely from
    being textually closer to the comma-separated DB spelling. Since
    `artist_relevance` is compared *before* `quality` in score_result()'s sort
    tuple, that was enough to pick the live recording over the Topic-channel
    upload despite `quality` correctly ranking them the other way around
    (+3.33 vs -2.77) — see docs/agent-notes/youtube-search-matching.md's
    "SLAUGHTER TO PREVAIL" case for the same tuple-ordering failure shape.
    Used as an extra `max()` candidate in `_artist_relevance_for()`, not a
    destructive rewrite, so this only ever adds a way to match, never
    loosens what already matched.
    """
    return re.sub(r"\s+", " ", re.sub(r"[,/&]", " ", text)).strip()


def _artist_channel_handle_match(expected: str, channel_clean: str) -> float:
    """A channel *handle* often concatenates the artist name with a suffix
    and no separator at all (e.g. `"sunsaymusic"`, or the already-documented
    `"ElvisCrespovevo"` tag in the Suavemente case) — squeezed together with
    no word boundary, `_text_containment()`'s whole-word requirement can
    never match it (`\\bsunsay\\b` doesn't match inside "sunsaymusic": there's
    no boundary before the "m"). Real case: SunSay's real channel is
    literally `"sunsaymusic"` — the correct, bare-title Topic-style upload
    there only ever scored artist_relevance ~0.71 via plain similarity(),
    while a wrong candidate whose own *title* spells "SunSay" as a separate
    word scored a full 1.0 via ordinary containment. Since artist_relevance
    is compared *before* `quality` in score_result()'s sort tuple, the wrong
    candidate won even though `quality` (view count, official-release
    metadata) decisively favored the right one. Squeeze both sides (strip
    spaces) and check a plain prefix match instead — safe because it only
    ever adds a match for a channel that starts with the *exact* artist
    name, never loosens an existing match.
    """
    squeezed_expected = re.sub(r"\s+", "", expected.lower())
    squeezed_channel = re.sub(r"\s+", "", channel_clean.lower())
    if squeezed_expected and squeezed_channel.startswith(squeezed_expected):
        return 1.0
    return 0.0


# Separators between individual names in a combined "A ft. B" / "A, B, C"
# artist field. Deliberately case-insensitive and tolerant of an optional
# trailing "." ("feat"/"feat.", "ft"/"ft.") — real DB/candidate spellings
# aren't consistent about the period.
_MULTI_ARTIST_SEPARATOR = re.compile(r"\s*(?:,|/|&|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bx\b|\bvs\.?\b)\s*", re.IGNORECASE)


def _multi_artist_tokens(expected: str) -> list[str]:
    """Split a combined multi-artist field into its individual name tokens —
    only returned when there genuinely are 2+ names (a plain single-artist
    name with no separator returns `[]`), so callers can treat an empty
    result as "not a collab field, don't apply this mechanism".
    """
    parts = [p.strip() for p in _MULTI_ARTIST_SEPARATOR.split(expected) if p.strip()]
    return parts if len(parts) > 1 else []


def _multi_artist_names_all_present(expected: str, haystack: str) -> float:
    """1.0 when every individual name in a combined "A ft. B" artist field is
    present (as a whole word/phrase, via `_text_containment()`) *somewhere*
    in `haystack` — not necessarily adjacent to each other or in the DB's own
    order, unlike every other check in `_artist_relevance_for()`. Real
    uploads routinely split a "ft."/"feat." credit across the song title
    itself (e.g. `"Drenchill - Freed from Desire ft. Indiiana"` — the primary
    and featured artist end up on opposite sides of the title), and the
    connector word varies ("ft." vs "feat." vs a bare comma) — no amount of
    whole-phrase containment or punctuation normalization
    (`_normalize_multi_artist_punctuation()`) can bridge either gap, since
    both assume the names stay adjacent.

    Real case: `Drenchill ft. Indiiana - Freed From Desire` — every real
    upload spelled the credit differently enough (`"... ft. Indiiana"`,
    `"... (feat. Indiiana)"`, `"..., Indiiana"`) to score only 0.44-0.69
    artist_relevance, while a `"(Bass Boosted)"` reupload happened to spell
    the whole DB phrase `"Drenchill ft. Indiiana"` verbatim and contiguously,
    scoring a clean 1.0 via ordinary containment — enough to win outright
    since artist_relevance sorts before `quality` (which already correctly
    penalized `"Bass Boosted"`, -1.64 vs the real uploads' 3.4-5.4).

    Deliberately requires *every* token present, not just one — a solo track
    by only the featured artist (or only the primary one) still shouldn't
    match a search for the collab. Only ever adds a way to score 1.0, so this
    can't loosen an already-correct match, only rescue one that plain
    containment/similarity structurally can't reach.
    """
    tokens = _multi_artist_tokens(expected)
    if not tokens:
        return 0.0
    if all(_text_containment(tok, haystack) >= 1.0 for tok in tokens):
        return 1.0
    return 0.0


def _artist_relevance_for(name: str, candidate_clean: str, channel_clean: str) -> float:
    """artist_relevance for one candidate name against one artist name —
    factored out so score_result() can take the max across the DB artist
    name and any known synonyms without duplicating this logic per name.

    Also tries a diacritic-folded comparison (`_fold_diacritics()`, the same
    helper title relevance already uses) — unlike title relevance, this
    wasn't previously applied here at all. Real case: `La Vida Bohème -
    Radio Capital` (DB has the accented "è") — the real official upload is
    hosted on a label channel ("Nacional Records", contributing nothing to
    artist_relevance), so its only signal is a `similarity()` ratio against
    the video's own title text ("La Vida Boheme - Radio Capital", no accent),
    diluted further by the trailing song title — without folding, that
    scored only 0.59. A lower-quality "(En Vivo)" reupload, hosted on the
    artist's *own* channel (bare "La Vida Boheme", no accent, no decoration
    at all), scored 0.93 from that clean channel-name match alone — enough to
    win on artist_relevance before `quality` (which correctly favored the
    official video, 4.29 vs 1.20) was ever consulted. Folding closes the
    accent gap for both the label-hosted and artist-hosted case, so this only
    ever adds a way to match — the same "extra max() branch" pattern already
    used for punctuation normalization above.

    Also tries `_multi_artist_names_all_present()` — see that function's own
    docstring for why a combined "A ft. B" field needs its names checked
    independently, not just as one phrase, once real uploads split them
    across the song title itself.
    """
    expected = _relevance_text(name)
    expected_normalized = _normalize_multi_artist_punctuation(expected)
    expected_folded = _fold_diacritics(expected)
    relevance = 0.0
    if expected and candidate_clean:
        candidate_normalized = _normalize_multi_artist_punctuation(candidate_clean)
        candidate_folded = _fold_diacritics(candidate_clean)
        relevance = max(
            similarity(expected, candidate_clean),
            _text_containment(expected, candidate_clean),
            similarity(expected_normalized, candidate_normalized),
            _text_containment(expected_normalized, candidate_normalized),
            similarity(expected_folded, candidate_folded),
            _text_containment(expected_folded, candidate_folded),
        )
    if expected and channel_clean:
        channel_normalized = _normalize_multi_artist_punctuation(channel_clean)
        channel_folded = _fold_diacritics(channel_clean)
        relevance = max(
            relevance,
            similarity(expected, channel_clean),
            _text_containment(expected, channel_clean),
            similarity(expected_normalized, channel_normalized),
            _text_containment(expected_normalized, channel_normalized),
            similarity(expected_folded, channel_folded),
            _text_containment(expected_folded, channel_folded),
            _artist_channel_handle_match(expected, channel_clean),
        )
    relevance = max(
        relevance,
        _multi_artist_names_all_present(expected, f"{candidate_clean} {channel_clean}".lower()),
    )
    return relevance


def _min_relevance_for(title: str) -> float:
    """Short titles need a near-exact match.

    A whole-string similarity() ratio is unreliable for short strings: e.g.
    similarity("nix", "netflix top 10 trailer compilation") is already 0.67
    just from incidental character overlap, well past a loose bar. Scale the
    required relevance up for short titles the same way
    scaled_similarity_threshold() already does for short artist names
    elsewhere in this codebase.
    """
    expected_title = _relevance_text(title)
    return scaled_similarity_threshold(expected_title, expected_title, MIN_RELEVANCE)


def _is_topic_channel(channel: str) -> bool:
    """YouTube auto-generates a "<Artist> - Topic" channel per artist for
    official, single-track audio uploads (title = bare song title, no
    artist prefix). These are exactly the plain, unedited version a music
    library wants, so they're worth a quality bonus like HQ_KEYWORDS.

    Real gap this doesn't cover: yt-dlp's `channel` field doesn't reliably
    carry the "- Topic" suffix even for a genuine auto-generated Topic
    channel — some report the underlying default name literally (e.g.
    `"Вольны Хор - Topic"`), but others report a claimed/branded custom name
    with no "Topic" wording anywhere (e.g. Al Di Meola's Topic channel is
    just `"Al Di Meola"`, SunSay's is `"sunsaymusic"`), even though the
    YouTube website displays both as "<Artist> – Topic". This function only
    catches the first kind — see `_is_official_release()` below for the
    signal that catches both.
    """
    return channel.strip().lower().replace(" ", "").endswith("-topic")


def _is_official_release(channel: str, track: str | None) -> bool:
    """True for a genuine official/auto-generated music distribution upload,
    catching both the channels `_is_topic_channel()` recognizes by name and
    the ones it can't (see its docstring).

    `track` is yt-dlp-only (its search JSON exposes YouTube Music's own
    `track`/`artists`/`album` metadata for free; the Data API's
    `search().list()` doesn't) and is populated *only* for tracks actually
    distributed to YouTube by a label/aggregator — every such candidate's
    `description` literally starts "Provided to YouTube by ..." / contains
    "Auto-generated by YouTube.". A fan reupload, live bootleg, or cover
    never has it, `None` reliably. Verified directly against real yt-dlp
    output for three real cases (Al Di Meola - Double Concerto, SunSay - В
    твоих глазах сияю я, SunSay - Немовля): every wrong pick in production
    had `track=None`, every correct pick had `track` set — including cases
    where the wrong pick had *far* more views (a live Budapest recording,
    91K views, beat the real Topic-channel upload, 10.9K views, purely
    because `_is_topic_channel()` didn't recognize `channel="Al Di Meola"`
    as a Topic channel at all and no other signal offset the view-count
    gap). This is a stronger, more general signal than any keyword list or
    the channel-name heuristic, and previously went unused entirely.
    """
    if channel and _is_topic_channel(channel):
        return True
    return bool(track)


def _popularity_bonus(view_count: int | None) -> float:
    """Log-scaled quality bonus from view count — a low-effort reupload
    (an excerpt, an instrumental rip, a random remix) is almost always
    watched far less than the real/official upload of the same song, even
    when keyword-based quality scoring can't tell them apart (e.g. neither
    title mentions "audio"/"remix" at all). View count spans several orders
    of magnitude (tens to millions), so it's log-scaled and centered on
    ~1000 views (log10 == 3) to land in roughly the same range as the
    existing +/-1..4 keyword-based quality adjustments, rather than
    swamping them.
    """
    if not view_count or view_count <= 0:
        return 0.0
    return math.log10(view_count) - 3


def score_result(
    candidate_title: str,
    artist: str,
    title: str,
    channel: str = "",
    view_count: int | None = None,
    tags: list[str] | None = None,
    artist_synonyms: str | None = None,
    track: str | None = None,
) -> tuple[float, float, float]:
    """Score a candidate video: (title relevance, artist relevance, quality).

    Acceptance is gated on title relevance alone (see _min_relevance_for) —
    the title is what distinguishes the specific song, and folding the
    artist name into that comparison lets a wrong song by the same (or
    similarly spelled) artist score deceptively high. Artist relevance is
    kept only as a tiebreaker: when multiple candidates clear the title bar
    (e.g. the same generic title from different artists), prefer the one
    whose artist also matches. `channel` (the uploader/channel name) is
    checked too, not just the video title — a "<Artist> - Topic" upload's
    title is deliberately just the song title with no artist mentioned at
    all, so the title alone would otherwise make a correct match look like
    it has no artist relevance.

    `view_count` is optional because it's only free from yt-dlp's search
    JSON — the YouTube Data API's search().list() doesn't return it (getting
    it there needs a separate videos().list() call per candidate, burning
    quota), so API-sourced candidates just don't get a popularity term.

    `tags` (also yt-dlp-only/free) are folded into the keyword scan alongside
    the title, not used for relevance — a live recording's *title* often
    carries no signal at all (e.g. "Oberschlesien - Król Olch #Woodstock2016"
    has no English "live"/"concert" keyword), but its uploader-assigned tags
    did: "...na żywo" (Polish for "live") plus repeated festival names, while
    the actual studio upload's tags were clean. Deliberately tags only, not
    `description` — tags are curated keywords, description is freeform prose
    where "live" could appear in unrelated boilerplate (tour dates, etc.) and
    false-positive.

    `title`'s bracket content also gets a second look after relevance is
    computed: if it contains a specific selector (a named remix/collaborator,
    not just generic branding — see _selector_tokens()), a candidate whose
    own title or tags actually mention it earns SELECTOR_MATCH_BONUS. This
    doesn't affect the relevance gate itself, only the tiebreak — if no
    candidate mentions the specific remix asked for, we still want to fall
    back to whatever's available rather than reject everything outright.

    `artist_synonyms` (the artists table's `synonyms` column, comma-separated
    — see _parse_synonyms()) is checked alongside `artist` for
    artist_relevance, taking the max across all names. No amount of
    string-similarity tuning can bridge a real name change — this is the
    only mechanism that can.

    `track` (yt-dlp-only/free, like `view_count`/`tags`) is YouTube Music's
    own metadata for a genuinely label-distributed release — see
    `_is_official_release()`. It's an alternative to `_is_topic_channel()`'s
    channel-name-suffix check, not an addition to it: both grant the same
    quality bonus, since yt-dlp's `channel` field doesn't reliably carry the
    "- Topic" suffix even for a real Topic channel (see that function's
    docstring), and `track` catches exactly the cases it misses.
    """
    expected_title = _relevance_text(title)
    candidate_clean = _relevance_text(candidate_title)
    if expected_title and candidate_clean:
        # Both variance-tolerance transforms are folded into one "maximally
        # normalized" pass rather than kept as separate max() branches — a
        # title can need both at once (a doubled letter *and* a missing
        # accent), and running them together costs nothing extra since
        # neither transform is destructive when the other's pattern isn't
        # present (collapsing is a no-op with no repeated letters; folding
        # is a no-op with no diacritics).
        expected_title_normalized = _collapse_repeated_letters(_fold_diacritics(expected_title))
        candidate_clean_normalized = _collapse_repeated_letters(_fold_diacritics(candidate_clean))
        relevance = max(
            similarity(expected_title, candidate_clean),
            _text_containment(expected_title, candidate_clean),
            similarity(expected_title_normalized, candidate_clean_normalized),
            _text_containment(expected_title_normalized, candidate_clean_normalized),
        )
    else:
        relevance = 0.0
    if any(_keyword_present(kw, candidate_title.lower()) for kw in NOT_THE_SONG_KEYWORDS):
        relevance = 0.0

    channel_clean = _relevance_text(channel)
    artist_relevance = max(
        _artist_relevance_for(name, candidate_clean, channel_clean)
        for name in [artist, *_parse_synonyms(artist_synonyms)]
    )

    # Title-only text for most keyword scanning. Uploader-supplied `tags`
    # are much noisier than they first looked: real case, "Elvis Crespo -
    # Suavemente"'s official Vevo upload carries generic, broadly-cast SEO
    # tags ("remix", "karaoke", "instrumental") that don't describe *this*
    # upload's content at all — they're just adjacent searches the label
    # also wants to rank for. Scanning those against LQ_KEYWORDS incorrectly
    # penalized the real video (and, since an LQ hit suppresses the whole HQ
    # bonus block, cost it the "official" bonus too — a 5-point swing,
    # enough to lose to an unrelated lower-view collab). tags stay in scope
    # only for STRONG_LQ_KEYWORDS (see below) and the selector-hint bonus,
    # both of which look for specific signals unlikely to be blanket
    # SEO-stuffed the way generic descriptor words are.
    title_lower = candidate_title.lower()
    keyword_text = title_lower
    if tags:
        keyword_text += " " + " ".join(tags).lower()

    def _non_overlapping_hits(keywords: list[str], text: str) -> list[str]:
        # Some keyword-list entries are substrings of others in the same list
        # (e.g. "audio" inside "official audio", "mv" inside "official mv",
        # "orchestra" inside "orchestral") — matching both against the same
        # title text double-counts what is really a single signal. Drop any
        # hit that's wholly contained in another hit from the same list
        # before scoring.
        hits = [kw for kw in keywords if _keyword_present(kw, text)]
        return [kw for kw in hits if not any(kw != other and kw in other for other in hits)]

    lq_hits = _non_overlapping_hits(LQ_KEYWORDS, title_lower)
    # STRONG_LQ_KEYWORDS deliberately still scans tags too — that's the
    # entire reason it exists (OBERSCHLESIEN's live festival tag ("na żywo")
    # wasn't in the title at all), and "live"/"na żywo"/"woodstock" are
    # specific enough signals that a channel is unlikely to blanket-tag them
    # onto an unrelated upload the way "remix"/"karaoke" get SEO-stuffed.
    strong_lq_hits = [kw for kw in STRONG_LQ_KEYWORDS if _keyword_present(kw, keyword_text)]
    quality = 0
    # An "Official Audio" / "HQ" label only means the video is well-produced,
    # not that it's the plain studio version — a professionally released
    # remix or demo can carry that label too. If the title already signals
    # an alternate arrangement (LQ_KEYWORDS/STRONG_LQ_KEYWORDS), don't let
    # the HQ bonus offset that penalty, or a polished remix/dub upload
    # out-scores the real thing (e.g. Foals - 2001: a "(Dan Carey Dub) -
    # Official Audio" reupload was outranking the actual official video
    # because "official audio" gave +4 from the double-count bug above,
    # dwarfing a single -1 dub/remix hit).
    if not lq_hits and not strong_lq_hits:
        quality += 2 * len(_non_overlapping_hits(HQ_KEYWORDS, title_lower))
    quality -= VIDEO_PENALTY * len(_non_overlapping_hits(VIDEO_KEYWORDS, title_lower))
    quality -= len(lq_hits)
    quality -= STRONG_LQ_PENALTY * len(strong_lq_hits)
    # Same reasoning as the HQ bonus above, extended to this trust signal:
    # being a genuine label/aggregator-distributed release doesn't mean it's
    # the plain canonical version — a label can distribute a remix or club
    # edit through the same Topic-channel/`track`-metadata pipeline as the
    # original. Real case: Betoko - Breaking (Original Mix) — a "(Club
    # Edit)" upload had its own `track` metadata (genuinely official-release,
    # not a fan reupload) and this bonus applied unconditionally, outscoring
    # the real "(OKO Recordings)" upload (no official-release metadata at
    # all, just the artist's own channel) despite both tying on
    # relevance/artist_relevance. Withholding this bonus when the title
    # already signals an alternate version — same gate as the HQ block — lets
    # the LQ_KEYWORDS penalty actually decide instead of being swamped by an
    # unconditional trust bonus for the wrong version.
    if _is_official_release(channel, track) and not lq_hits and not strong_lq_hits:
        quality += 2
    quality += HIGH_TRUST_BONUS * len([kw for kw in HIGH_TRUST_KEYWORDS if _keyword_present(kw, title_lower)])
    for hint in _bracket_selector_hints(title):
        tokens = _selector_tokens(hint)
        if tokens and all(re.search(r"\b" + re.escape(tok) + r"\b", keyword_text) for tok in tokens):
            quality += SELECTOR_MATCH_BONUS
            break
    quality += _popularity_bonus(view_count)
    return relevance, artist_relevance, quality


def _select_best_candidate(
    candidates: list[tuple[tuple[float, float, float], str, str, bool]], title: str
) -> tuple[str | None, float, bool]:
    """Pick the winning (video_id, best_relevance, is_official) from a
    (score, video_id, video_title, is_official) list, gated on both title
    relevance (_min_relevance_for) and MIN_ARTIST_RELEVANCE — see that
    constant's docstring for why the artist floor is needed on top of the
    (deliberately title-only) relevance gate. Shared by the yt-dlp and Data
    API search paths so both reject the same way and both report
    `is_official` (see _is_official_release()) for the alternate-script
    retry (transliteration.py) to decide against: a winning candidate that
    isn't a confirmed official release is exactly the case where the *real*
    official upload might be hiding under the other script — see the
    "Cicha, jak maja śmierć"/"Ihołki" real cases in the conversation this
    was built from, where the genuine official upload (Cyrillic title)
    scored too low on relevance to even be considered against the DB's
    Lacinka title, leaving only live reuploads to pick from.

    `best_relevance` is 0.0 and `is_official` is False when no candidate
    even clears the artist floor — there's no meaningful "closest" title
    relevance to report in that case since every title-only match was to an
    implausible artist.
    """
    plausible = [c for c in candidates if c[0][1] >= MIN_ARTIST_RELEVANCE]
    if not plausible:
        print("  ✖ No relevant match (no candidate had a plausible artist match)")
        return None, 0.0, False

    plausible.sort(key=lambda x: x[0], reverse=True)
    (best_relevance, best_artist_relevance, best_quality), best_id, best_title, is_official = plausible[0]
    required = _min_relevance_for(title)
    if best_relevance < required:
        print(f"  ✖ No relevant match (closest: {best_title}, relevance={best_relevance:.2f}, needed {required:.2f})")
        return None, best_relevance, False
    print(f"  ✔ Best match (relevance={best_relevance:.2f}, score={best_quality}): {best_title} [{best_id}]")
    return best_id, best_relevance, is_official


def _run_ytdlp_search(query: str, max_results: int, timeout: int = 30):
    """One yt-dlp `--dump-json` search attempt. Returns the completed process,
    or None on timeout/missing-binary (a real failure, not worth retrying)."""
    try:
        return subprocess.run(
            [
                "yt-dlp",
                "--dump-json",
                "--no-playlist",
                f"ytsearch{max_results}:{query}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  ✖ yt-dlp unavailable: {e}")
        return None


def _search_video_ytdlp_once(
    artist: str, title: str, max_results: int, artist_synonyms: str | None, max_attempts: int
) -> tuple[str | None, float, bool]:
    """One yt-dlp search-and-score attempt for one exact title string.

    Returns (video_id, best_relevance, is_official) — video_id is None when
    nothing cleared _min_relevance_for(title), but best_relevance still
    reports how close the closest candidate got. `is_official` (see
    _is_official_release()) tells a caller whether the winning pick is a
    confirmed official release, so it can decide whether a transliterated
    retry (see search_video_ytdlp) is worth attempting.

    `max_attempts` retries a returncode!=0/empty-stdout response once before
    giving up. This is a transient-failure retry, not a wider net: yt-dlp's
    anonymous scraping search is confirmed non-deterministic run-to-run for
    the exact same query — real case, "Waglewski, Fisz, Emade - Bóg" returned
    zero results in one production run (forcing a fallback to the API, which
    only had a "(live Frytka Off)" reupload to offer) but returned 7 good
    candidates, including the correct Topic-channel upload, moments later on
    an identical retry. One retry, no backoff, is deliberately cheap — it
    only needs to survive a momentary hiccup, not a real outage (the
    subprocess timeout/API fallback already handle that).

    Known limitation this can't fix: an age-restricted video (e.g. Taco
    Hemingway - "Fuck Your List", flagged by YouTube's own community
    guidelines) is invisible to anonymous `ytsearch` regardless of retries —
    verified directly against every yt-dlp `player_client` option (web,
    tv_embedded, android, ios, mweb, ...), none surface it, while it's the
    #1 organic result on youtube.com's own search for a signed-out session.
    This is the same failure mode already documented for `"<name> Sex ..."`
    queries, just a different trigger (profanity vs. the word "Sex") — no
    retry count or client switch bypasses it; only a signed-in/age-verified
    session could, which the API fallback's OAuth session may or may not
    have depending on the authenticated account's own age-verification
    status. `LQ_KEYWORDS` gaining "tłumaczenie"/"lyrics translation" is what
    actually helps this specific case — it stops the API fallback's best
    candidate (a lyrics-translation channel, not the song) from winning by
    default once nothing better is available.
    """
    query = f"{artist} - {title}"
    print(f"\nSearching via yt-dlp for: {query}")

    result = None
    for attempt in range(max_attempts):
        result = _run_ytdlp_search(query, max_results)
        if result is None:
            return None, 0.0, False  # timeout/missing binary — not a transient case, don't retry
        if result.returncode == 0 and result.stdout.strip():
            break
        if attempt + 1 < max_attempts:
            print("  ⚠ yt-dlp returned no results, retrying once...")

    if result.returncode != 0 or not result.stdout.strip():
        print("  ✖ yt-dlp returned no results")
        return None, 0.0, False

    candidates = []
    for line in result.stdout.strip().splitlines():
        try:
            info = json.loads(line)
            video_id = info.get("id")
            video_title = info.get("title", "")
            channel = info.get("channel") or info.get("uploader") or ""
            view_count = info.get("view_count")
            tags = info.get("tags")
            track = info.get("track")
            if video_id:
                candidates.append(
                    (
                        score_result(video_title, artist, title, channel, view_count, tags, artist_synonyms, track),
                        video_id,
                        video_title,
                        _is_official_release(channel, track),
                    )
                )
        except json.JSONDecodeError:
            continue

    if not candidates:
        return None, 0.0, False

    return _select_best_candidate(candidates, title)


def search_video_ytdlp(
    artist: str,
    title: str,
    max_results: int = 8,
    artist_synonyms: str | None = None,
    max_attempts: int = 2,
    language: str | None = None,
) -> str | None:
    """Search YouTube using yt-dlp (no API quota used). See
    _search_video_ytdlp_once() for the actual search-and-score logic.

    `language` (the songs table's `language` column) enables a second
    attempt with the title transliterated into the other script — triggered
    when this attempt found nothing at all, OR found something that isn't a
    confirmed official release (see _is_official_release()): a non-official
    winner is exactly the situation where the genuine official upload might
    be sitting under the other script, invisible to this attempt's relevance
    check (real case: "Cicha, jak maja śmierć"/"Ihołki" — the official
    Cyrillic-titled upload scored too low against the Lacinka DB title to
    even be considered, leaving only live reuploads to pick from). If the
    alt attempt also fails to find an official release, the original pick
    (if any) is kept — it's still better than nothing.
    """
    video_id, best_relevance, is_official = _search_video_ytdlp_once(
        artist, title, max_results, artist_synonyms, max_attempts
    )
    if video_id and is_official:
        return video_id

    if is_transliterable(language):
        alt_title = transliterate(title, language)
        if alt_title and alt_title != title:
            print(f"  ↩ Retrying yt-dlp with transliterated title: {alt_title}")
            alt_video_id, _, alt_official = _search_video_ytdlp_once(
                artist, alt_title, max_results, artist_synonyms, max_attempts
            )
            if alt_video_id and (alt_official or not video_id):
                return alt_video_id

    return video_id


def _search_video_api_once(
    youtube, artist: str, title: str, artist_synonyms: str | None
) -> tuple[str | None, float, bool]:
    """One YouTube Data API search-and-score attempt for one exact title
    string. Returns (video_id, best_relevance, is_official) — see
    _search_video_ytdlp_once(). The Data API never exposes yt-dlp's `track`
    metadata, so `is_official` here can only ever come from
    _is_topic_channel()'s channel-name check, never the track-based signal."""
    query = f"{artist} - {title}"
    print(f"  Searching API for: {query}")

    try:
        response = youtube.search().list(
            part="snippet",
            q=query,
            type="video",
            maxResults=8,
            videoCategoryId="10",  # Music category
            safeSearch="none",
        ).execute()

    except HttpError as e:
        if e.resp.status == 403 and b"quotaExceeded" in e.content:
            print("🛑 YouTube API quota exceeded. Stopping.")
            raise SystemExit(1)
        raise

    items = response.get("items", [])
    if not items:
        print("  ✖ No results found")
        return None, 0.0, False

    candidates = [
        (
            score_result(
                item["snippet"]["title"],
                artist,
                title,
                item["snippet"].get("channelTitle", ""),
                artist_synonyms=artist_synonyms,
            ),
            item["id"]["videoId"],
            item["snippet"]["title"],
            _is_official_release(item["snippet"].get("channelTitle", ""), None),
        )
        for item in items
    ]
    return _select_best_candidate(candidates, title)


def search_video(
    youtube, artist: str, title: str, artist_synonyms: str | None = None, language: str | None = None
) -> str | None:
    """Search for a video, preferring HQ audio. Uses yt-dlp first, YT API as fallback.

    `language` (the songs table's `language` column) enables a second search
    attempt, with the title transliterated into the other script, for
    languages registered in transliteration.py — see search_video_ytdlp()'s
    docstring for the exact trigger condition (nothing found, or the winner
    isn't a confirmed official release).
    """

    # Try yt-dlp first — free, no quota
    video_id = search_video_ytdlp(artist, title, artist_synonyms=artist_synonyms, language=language)
    if video_id:
        return video_id

    # Fallback: YouTube Data API
    print("  ↩ Falling back to YouTube API...")
    video_id, best_relevance, is_official = _search_video_api_once(youtube, artist, title, artist_synonyms)
    if video_id and is_official:
        return video_id

    if is_transliterable(language):
        alt_title = transliterate(title, language)
        if alt_title and alt_title != title:
            print(f"  ↩ Retrying YouTube API with transliterated title: {alt_title}")
            alt_video_id, _, alt_official = _search_video_api_once(youtube, artist, alt_title, artist_synonyms)
            if alt_video_id and (alt_official or not video_id):
                return alt_video_id

    return video_id


def is_video_id_valid(video_id: str | None, timeout: int = 20) -> bool:
    """Check whether a stored YouTube video ID still resolves to a watchable
    video.

    `Song.youtube_video_id` (whether set manually or by `save_video_id_to_song()`
    below) can go stale after the fact — the video gets deleted, made
    private, or taken down — and trusting it blindly would keep adding a dead
    link to every future playlist instead of falling back to a fresh search.
    Uses yt-dlp (`--simulate`, no download) rather than the Data API so
    validating a stored link doesn't burn API quota, consistent with
    `search_video()`'s yt-dlp-first preference.
    """
    if not video_id:
        return False
    try:
        result = subprocess.run(
            ["yt-dlp", "--simulate", "--no-warnings", f"https://www.youtube.com/watch?v={video_id}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        _logger.warning(f"Could not validate video_id={video_id} ({e}) — assuming it's still valid")
        return True

    if result.returncode != 0:
        error_output = (result.stderr or result.stdout or "").strip().splitlines()
        _logger.info(f"video_id={video_id} failed validation: {error_output[-1] if error_output else 'unknown error'}")
        return False

    return True


def save_video_id_to_song(song, video_id: str | None) -> None:
    """Persist a resolved YouTube video ID onto the song's DB record, or
    clear a stale one when `video_id` is `None`.

    Called by `create_yt_playlist()` whenever `search_video()` finds a match
    for a song, so the video only ever needs to be found once — future
    playlist runs pick it up straight from `Song.youtube_video_id`, the same
    "already have a video_id, skip search" path a manually-set override
    already uses (see docs/agent-notes/youtube-search-matching.md). Also
    called with `video_id=None` to wipe a stored link that `is_video_id_valid()`
    just found dead (deleted/private on YouTube's end) and that a fallback
    search couldn't replace — otherwise the dead link would sit in the DB
    and get re-validated (and fail again) on every future run.
    """
    if song.youtube_video_id == video_id:
        return
    song.youtube_video_id = video_id
    submit_global_database_session()
    if video_id:
        _logger.info(f"Saved video_id={video_id} to DB for {song.artist.name} - {song.title}")
    else:
        _logger.info(f"Cleared stale video_id from DB for {song.artist.name} - {song.title}")


# ---------------------------------------------------------
# INSERTING VIDEOS WITH RETRY LOGIC
# ---------------------------------------------------------

def is_quota_exceeded(error: HttpError) -> bool:
    try:
        error_details = json.loads(error.content.decode("utf-8"))
        reason = error_details["error"]["errors"][0]["reason"]
        return reason == "quotaExceeded"
    except Exception:
        return False

def add_video_to_playlist(youtube, playlist_id: str, video_id: str) -> bool:
    """Add a video to playlist with retry logic for API conflicts."""

    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {
                "kind": "youtube#video",
                "videoId": video_id,
            }
        }
    }

    for attempt in range(3):
        try:
            youtube.playlistItems().insert(
                part="snippet",
                body=body
            ).execute()

            print("  ➕ Added to playlist")
            time.sleep(0.3)  # Give API breathing room
            return True

        except HttpError as e:
            if is_quota_exceeded(e):
                # Let the caller's `except HttpError` handle this the same
                # way it does for search-quota errors: save progress and
                # stop, instead of silently dropping this song.
                raise

            if e.resp.status in (409, 500, 503):
                print(f"  ⚠ API error {e.resp.status}, retry {attempt+1}/3 …")
                time.sleep(1 + attempt)
                continue

            print("  ✖ Failed permanently:", e)
            return False

    print("  ✖ Failed after retries")
    return False


def create_yt_playlist(song_list, playlist_name: str):
    youtube = get_youtube_service()

    # Load cache if it exists
    cache = load_cache()

    try:
        # -------------------
        # Handle playlist setup
        # -------------------
        if cache and cache.get("playlist_name") == playlist_name:
            choice = questionary.select(
                f"Unfinished playlist '{playlist_name}' found. What do you want to do?",
                choices=[
                    "Resume unfinished playlist",
                    "Start over (create new playlist)",
                    "Cancel",
                ]
            ).ask()

            if choice == "Resume unfinished playlist":
                print("📂 Resuming unfinished playlist")
                playlist_id = cache["playlist_id"]

            elif choice == "Start over (create new playlist)":
                clear_cache()
                playlist_id = create_playlist(
                    youtube,
                    title=playlist_name,
                    description="Generated automatically"
                )
                cache = init_cache(playlist_id, playlist_name, song_list)

            else:
                return

        else:
            playlist_id = create_playlist(
                youtube,
                title=playlist_name,
                description="Generated automatically"
            )
            cache = init_cache(playlist_id, playlist_name, song_list)

        if not cache:
            raise RuntimeError("Cache was not initialized")

        # Maps a cache key back to the actual Song DB object, so a
        # freshly-found video can be written back to Song.youtube_video_id
        # (see save_video_id_to_song()) once search_video() finds it.
        songs_by_key = {make_song_key(song.artist.name, song.title): song for song in song_list}

        # -------------------
        # Add videos to playlist
        # -------------------
        for key, entry in cache["songs"].items():
            if entry["added"]:
                continue

            artist = entry["artist"]
            title = entry["title"]
            artist_synonyms = entry.get("synonyms")
            language = entry.get("language")

            # Step 1: a video_id already on the cache entry (pre-filled from
            # Song.youtube_video_id at init_cache() time, or left over from a
            # previous, interrupted run) is trusted only after confirming it
            # still resolves — a link can go stale after it was set.
            video_id = entry.get("video_id")

            # NO_VIDEO_SENTINEL isn't a real id to validate — there's no
            # behavior difference from an empty/unset field yet (no code
            # currently branches on the two differently), so it's normalized
            # to "no stored link" here and falls through to a normal search
            # below, same as any other song without a stored video_id.
            if video_id == NO_VIDEO_SENTINEL:
                video_id = None

            stored_video_id_was_invalid = False
            if video_id:
                if is_video_id_valid(video_id):
                    _logger.info(f"Reusing stored video_id={video_id} for {artist} - {title}")
                else:
                    _logger.warning(f"Stored video_id={video_id} for {artist} - {title} is no longer valid — searching again")
                    print("  ⚠ Stored YouTube link looks invalid, searching again...")
                    video_id = None
                    stored_video_id_was_invalid = True

            # Step 2: no usable stored link — search as before.
            if not video_id:
                video_id = search_video(youtube, artist, title, artist_synonyms=artist_synonyms, language=language)
                if not video_id:
                    _logger.info(f"No video found for {artist} - {title}")
                    print("  ❌ No video found")
                    if stored_video_id_was_invalid:
                        # The old link is confirmed dead and nothing replaced
                        # it — clear it from both the cache and the DB so it
                        # doesn't keep getting re-validated (and failing
                        # again) on every future run.
                        entry["video_id"] = None
                        save_cache(cache)
                        song = songs_by_key.get(key)
                        if song is not None:
                            save_video_id_to_song(song, None)
                        else:
                            _logger.warning(f"Could not map {artist} - {title} back to a Song object — stale video_id not cleared from DB")
                    continue
                entry["video_id"] = video_id
                save_cache(cache)

                # Step 3: a newly-found video is saved back to the DB so
                # future playlist runs don't need to search for it again.
                song = songs_by_key.get(key)
                if song is not None:
                    save_video_id_to_song(song, video_id)
                else:
                    _logger.warning(f"Could not map {artist} - {title} back to a Song object — video_id not persisted to DB")

            if not add_video_to_playlist(youtube, playlist_id, video_id):
                # Not added — leave entry["added"] False so this song is
                # retried on the next run instead of being silently dropped.
                continue

            entry["added"] = True
            save_cache(cache)

    except HttpError as e:
        save_cache(cache)
        if is_quota_exceeded(e):
            print("\n🚫 YouTube API quota exceeded.")
            print("💾 Progress saved — you can resume later.")
            return
        raise

    except KeyboardInterrupt:
        save_cache(cache)
        print("\n⏸ Interrupted by user. Progress saved.")
        return

    # -------------------
    # Completed successfully
    # -------------------
    print("\nPlaylist creation complete!")
    clear_cache()

