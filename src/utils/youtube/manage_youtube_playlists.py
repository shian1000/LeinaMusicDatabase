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
import json


import json
from googleapiclient.errors import HttpError


SCOPES = ["https://www.googleapis.com/auth/youtube"]

TOKEN_PATH = ".secrets/token.json"
CLIENT_SECRET_PATH = ".secrets/client_secret.json"

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
# canonical arrangement".
LQ_KEYWORDS = ["concert", "tour", "performance", "session", "acoustic", "cover", "karaoke", "instrumental", "remix", "sped up", "slowed", "nightcore", "8d audio", "bass boosted", "demo", "dub", "orchestra", "orchestral", "react", "review", "teaser", "eurovision version", "high tone", "track by track"]
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
STRONG_LQ_KEYWORDS = ["live", "na żywo", "woodstock"]
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

# Unicode ranges for scripts written without spaces between words/characters
# (hiragana/katakana, CJK ideographs, hangul syllables). Titles in these
# scripts need substring-containment matching instead of whole-word regex
# matching, since there's no word boundary to anchor on.
_NO_SPACES_SCRIPT = re.compile(r"[぀-ヿ㐀-鿿가-힯]")



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
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


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
    library wants, so they're worth a quality bonus like HQ_KEYWORDS."""
    return channel.strip().lower().replace(" ", "").endswith("-topic")


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

    expected_artist = _relevance_text(artist)
    channel_clean = _relevance_text(channel)
    artist_relevance = 0.0
    if expected_artist and candidate_clean:
        artist_relevance = max(
            similarity(expected_artist, candidate_clean),
            _text_containment(expected_artist, candidate_clean),
        )
    if expected_artist and channel_clean:
        artist_relevance = max(
            artist_relevance,
            similarity(expected_artist, channel_clean),
            _text_containment(expected_artist, channel_clean),
        )

    keyword_text = candidate_title.lower()
    if tags:
        keyword_text += " " + " ".join(tags).lower()

    def _non_overlapping_hits(keywords: list[str]) -> list[str]:
        # Some keyword-list entries are substrings of others in the same list
        # (e.g. "audio" inside "official audio", "mv" inside "official mv",
        # "orchestra" inside "orchestral") — matching both against the same
        # title text double-counts what is really a single signal. Drop any
        # hit that's wholly contained in another hit from the same list
        # before scoring.
        hits = [kw for kw in keywords if kw in keyword_text]
        return [kw for kw in hits if not any(kw != other and kw in other for other in hits)]

    lq_hits = _non_overlapping_hits(LQ_KEYWORDS)
    strong_lq_hits = [kw for kw in STRONG_LQ_KEYWORDS if kw in keyword_text]
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
        quality += 2 * len(_non_overlapping_hits(HQ_KEYWORDS))
    quality -= VIDEO_PENALTY * len(_non_overlapping_hits(VIDEO_KEYWORDS))
    quality -= len(lq_hits)
    quality -= STRONG_LQ_PENALTY * len(strong_lq_hits)
    if channel and _is_topic_channel(channel):
        quality += 2
    quality += HIGH_TRUST_BONUS * len([kw for kw in HIGH_TRUST_KEYWORDS if kw in keyword_text])
    for hint in _bracket_selector_hints(title):
        tokens = _selector_tokens(hint)
        if tokens and all(re.search(r"\b" + re.escape(tok) + r"\b", keyword_text) for tok in tokens):
            quality += SELECTOR_MATCH_BONUS
            break
    quality += _popularity_bonus(view_count)
    return relevance, artist_relevance, quality

def search_video_ytdlp(artist: str, title: str, max_results: int = 8) -> str | None:
    """Search YouTube using yt-dlp (no API quota used)."""
    query = f"{artist} - {title}"
    print(f"\nSearching via yt-dlp for: {query}")

    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--dump-json",
                "--no-playlist",
                f"ytsearch{max_results}:{query}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  ✖ yt-dlp unavailable: {e}")
        return None

    if result.returncode != 0 or not result.stdout.strip():
        print("  ✖ yt-dlp returned no results")
        return None

    candidates = []
    for line in result.stdout.strip().splitlines():
        try:
            info = json.loads(line)
            video_id = info.get("id")
            video_title = info.get("title", "")
            channel = info.get("channel") or info.get("uploader") or ""
            view_count = info.get("view_count")
            tags = info.get("tags")
            if video_id:
                candidates.append(
                    (
                        score_result(video_title, artist, title, channel, view_count, tags),
                        video_id,
                        video_title,
                    )
                )
        except json.JSONDecodeError:
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    (best_relevance, best_artist_relevance, best_quality), best_id, best_title = candidates[0]
    required = _min_relevance_for(title)
    if best_relevance < required:
        print(f"  ✖ No relevant match (closest: {best_title}, relevance={best_relevance:.2f}, needed {required:.2f})")
        return None
    print(f"  ✔ Best match (relevance={best_relevance:.2f}, score={best_quality}): {best_title} [{best_id}]")
    return best_id

def search_video(youtube, artist: str, title: str) -> str | None:
    """Search for a video, preferring HQ audio. Uses yt-dlp first, YT API as fallback."""

    # Try yt-dlp first — free, no quota
    video_id = search_video_ytdlp(artist, title)
    if video_id:
        return video_id

    # Fallback: YouTube Data API
    print("  ↩ Falling back to YouTube API...")
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
        return None

    candidates = [
        (
            score_result(item["snippet"]["title"], artist, title, item["snippet"].get("channelTitle", "")),
            item["id"]["videoId"],
            item["snippet"]["title"],
        )
        for item in items
    ]
    candidates.sort(key=lambda x: x[0], reverse=True)
    (best_relevance, best_artist_relevance, best_quality), best_id, best_title = candidates[0]
    required = _min_relevance_for(title)
    if best_relevance < required:
        print(f"  ✖ No relevant match (closest: {best_title}, relevance={best_relevance:.2f}, needed {required:.2f})")
        return None
    print(f"  ✔ Best match (relevance={best_relevance:.2f}, score={best_quality}): {best_title} [{best_id}]")
    return best_id


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

        # -------------------
        # Add videos to playlist
        # -------------------
        for key, entry in cache["songs"].items():
            if entry["added"]:
                continue

            artist = entry["artist"]
            title = entry["title"]

            video_id = entry.get("video_id")
            if not video_id:
                video_id = search_video(youtube, artist, title)
                if not video_id:
                    print("  ❌ No video found")
                    continue
                entry["video_id"] = video_id
                save_cache(cache)

            add_video_to_playlist(youtube, playlist_id, video_id)

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

