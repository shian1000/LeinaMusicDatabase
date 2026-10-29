import json
import re
import subprocess
import unicodedata

import questionary
from googleapiclient.errors import HttpError
from utils.common.output import review_header, show, styled

from utils.youtube.manage_youtube_playlists import (
    YouTubeLoginError,
    get_youtube_service,
    _is_topic_channel,
    _parse_synonyms,
    _run_ytdlp_search,
    _is_official_release,
    _log_ytdlp_errors,
    ytdlp_errors,
    NON_DECOMPOSING_LETTER_FOLDS,
    run_ytdlp,
    video_durations_via_api,
)
from utils.common.normalizer import name_key, split_artist_title
from utils.discoveries.import_engine import find_matching_artist, run_import_batch
from utils.database.database_getter import get_artists_from_db_session
from utils.database.latin_names import artist_match_synonyms
from utils.common.text_utils import strip_trailing_junk_suffix
from config.constants import YTDLP_PLAYLIST_LANG
from utils.common.debug import flog

# What YouTube reports as the title for a playlist entry that's no longer
# reachable (removed by the uploader, or made private) - these carry no
# usable metadata at all and are skipped outright rather than fed into the
# import batch as "unknown artist/title" entries.
PLACEHOLDER_TITLES = {"Private video", "Deleted video"}


def is_placeholder_title(title: str | None) -> bool:
    """A deleted / private video: the API titles it "Private video", yt-dlp's
    --flat-playlist "[Private video]"."""
    return (title or "").strip().strip("[]") in PLACEHOLDER_TITLES

# Trailing "(...)"/"[...]" annotations that don't identify a genuinely
# different version of the song - just upload metadata - so they're stripped
# from the imported title entirely (e.g. "Mr. Vain (Original Radio Edit)" ->
# "Mr. Vain"). Deliberately NOT included: anything that marks a real
# alternate version worth keeping distinguishable in the library - "Remix",
# "Live", "Acoustic", "Cover", "Extended", "Club Edit", a named remixer, or a
# "feat./ft." credit. Add new junk phrases here as they come up; matching is
# case-insensitive and only against the title's trailing bracket (see
# strip_junk_suffix()), so this never touches a bracket earlier in the title.
YOUTUBE_TITLE_JUNK_PHRASES = [
    "Official Video",
    "Official Audio",
    "Official Music Video",
    "Official Lyric Video",
    "Lyric Video",
    "Lyrics",
    "HD",
    "HQ",
    "Videoclip",
    "MusicVideo",
    "Video Clip",
    "Original Radio Edit",
    "Radio Edit",
    "Radio Version",
    "Album Version",
    "Original Mix",
    "Explicit",
    "Clean Version",
    "Edit",
]
_YOUTUBE_TITLE_JUNK_PHRASES_LOWER = {p.lower() for p in YOUTUBE_TITLE_JUNK_PHRASES}

# Unlike YOUTUBE_TITLE_JUNK_PHRASES above (which requires the bracket's
# *entire* content to match one known phrase exactly), these are single
# words that condemn the whole trailing bracket just by appearing in it
# anywhere, since the rest of the bracket is typically source-specific and
# not worth trying to enumerate - e.g. "(Pes 2009 Soundtrack)" doesn't match
# any whole phrase above, but the bracket as a whole still just says what
# the recording was used for, not a version of the song. Add new marker
# words here as they come up; matched as a whole word, case-insensitive.
YOUTUBE_TITLE_JUNK_MARKER_WORDS = [
    "Soundtrack",
    "Audio",
    "From",
    "Video",
    # Says who uploaded it again, not which version (real case:
    # "DISCO BANIA - BRUNETKI I BLONDYNKI (reupload HD)")
    "Reupload",
    "Re-upload",
]
_YOUTUBE_TITLE_JUNK_MARKER_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in YOUTUBE_TITLE_JUNK_MARKER_WORDS) + r")\b",
    re.IGNORECASE,
)

# Same trailing-only scope as YOUTUBE_TITLE_JUNK_MARKER_WORDS above, but for
# a release/recording year instead of a fixed word list - any bare 4-digit
# number condemns the whole trailing bracket (e.g. "Sierpień (2022)" ->
# "Sierpień"), since a year annotation is upload metadata, not part of the
# song's identity.
_YOUTUBE_TITLE_YEAR_RE = re.compile(r"\b\d{4}\b")

# Matches one "(...)"/"[...]"/"【...】" group (no nested brackets) at the
# very end of the string, plus any trailing whitespace. "【】" is the CJK
# "lenticular bracket" pair, used the same way "[...]" is in Western titles
# (e.g. a trailing "【Kan/Rom/Eng Lyrics】" annotation). The fullwidth
# "（）"/"［］" pairs come from CJK input methods (real case: "Fujii Kaze -
# Workin’ Hard（Official Video）") and are treated exactly like "()"/"[]".
_TRAILING_BRACKET_RE = re.compile(r"\s*[\(\[【（［]([^()\[\]【】（）［］]*)[\)\]】）］]\s*$")

# Straight and curly double-quote characters, plus the CJK "corner bracket"
# pair "『』" (used to set off a work's title, the way Western titles use
# double quotes - e.g. '『Cinderella by Cidergirl』'). Only double-quote-like
# characters are stripped (never single quotes/apostrophes, which routinely
# appear inside a title itself - e.g. "Rock 'n' Roll", "Don't Stop") -
# uploaders wrap the song name in them fairly often (e.g. 'Night Club -
# "Need You Tonight" (INXS cover)'), and that's never meaningful punctuation
# worth keeping in the stored title.
_DOUBLE_QUOTE_RE = re.compile(r'["“”„‟『』]')


def strip_quote_marks(title: str) -> str:
    """Drop any double-quote-like character from `title` and collapse the
    whitespace that removing them can leave behind.
    """
    title = _DOUBLE_QUOTE_RE.sub("", title or "")
    return re.sub(r"\s+", " ", title).strip()


# Unlike YOUTUBE_TITLE_JUNK_MARKER_WORDS above (only checked against the
# *trailing* bracket, to avoid nuking a genuine subtitle earlier in the
# title), these condemn a "(...)"/"[...]" group no matter where it sits -
# e.g. "Herr Mannelig (animation) - Żniwa - Polish version" has real title
# text (" - Żniwa - Polish version") after the junk bracket, so it would
# never be reached by the trailing-only check. Only add a word here when
# it's safe to assume it never appears inside a real subtitle.
YOUTUBE_TITLE_JUNK_MARKER_WORDS_ANYWHERE = [
    "Animation",
    "Official",
    "Lyrics",
    "Lyric",
    "Visualiser",
    "Video",
    "Clip",
    "Officiel",
    "Oryginał",
    "Subtitles",
    "4K",
    "Remaster",
    "Remastered",
    "Oficial",
    # Bollywood label uploads (real case: "T-Series - Criminal (Full Song)
    # Ra.One") - the whole track, as opposed to a teaser/promo cut.
    "Full Song",
]
_YOUTUBE_TITLE_JUNK_MARKER_ANYWHERE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in YOUTUBE_TITLE_JUNK_MARKER_WORDS_ANYWHERE) + r")\b",
    re.IGNORECASE,
)

# Matches one "(...)"/"[...]"/"【...】" group (no nested brackets) anywhere
# in the string, plus any whitespace right before it.
_ANY_BRACKET_RE = re.compile(r"\s*[\(\[【（［]([^()\[\]【】（）［］]*)[\)\]】）］]")


def strip_junk_brackets_anywhere(title: str) -> str:
    """Drop any "(...)"/"[...]" group whose content contains one of
    YOUTUBE_TITLE_JUNK_MARKER_WORDS_ANYWHERE, wherever in `title` it sits.
    """
    def _replace(match: re.Match) -> str:
        if _YOUTUBE_TITLE_JUNK_MARKER_ANYWHERE_RE.search(match.group(1)):
            return ""
        return match.group(0)

    title = _ANY_BRACKET_RE.sub(_replace, title or "")
    return re.sub(r"\s+", " ", title).strip()


# A "|" in a YouTube title is routinely used to tack on channel/label
# branding after the actual song info (e.g. "OMNIMAR - The Matrix (Official
# Video) | darkTunes Music Group") - never part of the song title itself, so
# everything from the first one onward is dropped. Doing this before
# strip_junk_suffix() also lets a junk bracket that only *looks* trailing
# once the branding is gone (like "(Official Video)" above) get caught by
# the existing trailing-bracket check.
# Look-alikes of "|" count too (real case: "Dizzko Bizkit aka Mejsi - Bál ⎮
# 2017", U+23AE): fullwidth "｜", box-drawing "│", broken bar "¦".
_PIPE_SUFFIX_RE = re.compile(r"\s*[|⎮｜│¦].*$")


def strip_pipe_suffix(title: str) -> str:
    """Drop a trailing "| ..." branding suffix from `title`, if present."""
    return _PIPE_SUFFIX_RE.sub("", title or "").strip()


# Uploaders routinely tack promo hashtags onto the end of a title (e.g.
# "Nocą (Official Audio) #Zostańwdomu #KulturalnaStrefa") - never part of the
# song title itself, so every "#word" token is dropped wherever it appears.
_HASHTAG_RE = re.compile(r"#\S+")


def strip_hashtags(title: str) -> str:
    """Drop every "#word" hashtag token from `title`."""
    title = _HASHTAG_RE.sub("", title or "")
    return re.sub(r"\s+", " ", title).strip()


# A title that is really an uploaded file name (real case: "Justyna
# Steczkowska - pejzaz_bez_ciebie.mpg"): a trailing media extension is
# dropped, and an underscore-joined name is turned back into words.
_MEDIA_EXTENSION_RE = re.compile(
    r"\.(?:mpg|mpeg|mp4|m4v|avi|wmv|flv|mkv|webm|mov|3gp|mp3|wav|flac|ogg|m4a|wma)\s*$",
    re.IGNORECASE,
)


def clean_file_name_title(text: str) -> str:
    """Undo file-name artefacts in `text`: drop a trailing media extension,
    and if the rest is words joined by "_" with no spaces, split them back
    apart - capitalising each word when it was written all lowercase
    ("pejzaz_bez_ciebie.mpg" -> "Pejzaz Bez Ciebie")."""
    text = _MEDIA_EXTENSION_RE.sub("", (text or "").strip()).strip()
    if "_" in text and " " not in text:
        words = [w for w in text.split("_") if w]
        if text.islower():
            words = [w[:1].upper() + w[1:] for w in words]
        text = " ".join(words)
    return text


# Japanese uploads credit a song as "<Artist>「<Title>」" with no " - " at
# all (real case, channel "GOKUMON(UchikubiGokumonDoukoukai)": "打首獄門同好会
# 「なつのうた」" - artist 打首獄門同好会, title なつのうた). Only "「」" - "『』"
# also sets off album/work names, so it's too ambiguous to split on.
_CORNER_BRACKET_CREDIT_RE = re.compile(r"^(.+?)\s*「([^「」]+)」$")


def split_corner_bracket_credit(title: str) -> tuple | None:
    """Return (artist, title) from a whole "<Artist>「<Title>」" title, or
    None if it isn't one - including when the bracket only holds a junk
    annotation (e.g. "Song「Lyric Video」")."""
    match = _CORNER_BRACKET_CREDIT_RE.match((title or "").strip())
    if not match:
        return None
    artist, song_title = match.group(1).strip(), match.group(2).strip()
    if not (artist and song_title) or _YOUTUBE_TITLE_JUNK_MARKER_ANYWHERE_RE.search(song_title):
        return None
    return artist, song_title


# A bare year tacked on after a dash, outside any bracket (real case, channel
# "VHS-Goldie": "Hank Williams Jr. - Mr. Weatherman - 1982"). Bracketed years
# are already handled by _YOUTUBE_TITLE_YEAR_RE in strip_junk_suffix().
_TRAILING_DASH_YEAR_RE = re.compile(r"\s+[-–—]\s+(?:19|20)\d{2}\s*$")


def strip_trailing_dash_year(title: str) -> str:
    """Drop a trailing " - 1982"-style year, unless it's the whole title."""
    return _TRAILING_DASH_YEAR_RE.sub("", title or "").strip() or (title or "")


# A quoted song title first, its performers in a "ft." bracket and the
# soundtrack/album after the dash (real case, "Interscope Records": "'City of
# Stars' (Duet ft. Ryan Gosling, Emma Stone) - La La Land Original Motion
# Picture Soundtrack"). Plain "Artist - Title" splitting would make the whole
# left side the artist.
_QUOTED_TITLE_FEAT_CREDIT_RE = re.compile(
    r"""^\s*['"“‘„](?P<title>[^'"“”‘’„]+)['"”’]\s*"""
    r"""\([^()]*?\b(?:ft\.?|feat\.?|featuring)\s+(?P<artists>[^()]+)\)"""
    r"""(?:\s+[-–—]\s+.*)?$""",
    re.IGNORECASE,
)


# What an uploader puts after " - " when the left side is the song's title
# and the artist is only the channel (real case, channel "Attic Ocean":
# "Glow - Lyric Video (English / French)"). A side that is all these words,
# brackets aside, is no title. One word alone counts only when no song is
# called that ("Video" by India.Arie, "Music", "Audio" are songs).
_JUNK_ONLY_WORDS = frozenset({
    "official", "officiel", "oficial", "music", "lyric", "lyrics", "video", "audio", "clip",
    "videoclip", "musicvideo", "visualizer", "visualiser", "hd", "hq", "4k",
})
_JUNK_ONLY_SINGLE_WORDS = frozenset({"lyrics", "videoclip", "musicvideo", "visualizer", "visualiser"})


def is_junk_only_title(text: str | None) -> bool:
    """Whether `text` (a video title's side) is only an upload annotation -
    "Lyric Video (English / French)", "Official Video", "Visualizer" - with
    no song title in it."""
    words = strip_hashtags(_ANY_BRACKET_RE.sub(" ", text or "")).lower().split()
    if not words or any(w not in _JUNK_ONLY_WORDS for w in words):
        return False
    return len(words) > 1 or words[0] in _JUNK_ONLY_SINGLE_WORDS


def split_quoted_title_feat_credit(title: str) -> tuple | None:
    """Return (artists, title) from a "'<Title>' (... ft. <Artists>) - ..."
    video title, or None if it isn't one. Several artists stay joined
    ("Ryan Gosling, Emma Stone") for the artist-split review."""
    match = _QUOTED_TITLE_FEAT_CREDIT_RE.match(title or "")
    if not match:
        return None
    return match.group("artists").strip(), match.group("title").strip()


def strip_junk_suffix(title: str) -> str:
    """Repeatedly strip a trailing bracket group from `title` as long as it's
    junk - either its content matches YOUTUBE_TITLE_JUNK_PHRASES exactly, it
    contains one of YOUTUBE_TITLE_JUNK_MARKER_WORDS anywhere, or it contains a
    bare 4-digit year - handles a title decorated with more than one such
    annotation (e.g. "Song (Official Video) (HD)"). Only ever touches the
    trailing bracket, never one earlier in the title (e.g. a genuine subtitle
    like "Sad Story (Out of Luck)" is left alone).
    """
    title = (title or "").strip()
    while True:
        match = _TRAILING_BRACKET_RE.search(title)
        if not match:
            break
        content = match.group(1).strip()
        is_exact_junk = content.lower() in _YOUTUBE_TITLE_JUNK_PHRASES_LOWER
        has_marker_word = bool(_YOUTUBE_TITLE_JUNK_MARKER_RE.search(content))
        has_year = bool(_YOUTUBE_TITLE_YEAR_RE.search(content))
        if not (is_exact_junk or has_marker_word or has_year):
            break
        title = title[:match.start()].strip()
    return title


# Opening->closing bracket characters that strip_artist_from_title() will
# drop along with an artist-name match they wrap entirely (e.g. the "【】" in
# "Ado - 【Ado】 unravel 歌いました" exist only to enclose the artist name, so
# leaving them behind after the name is removed would strand an empty
# "【】" in the title).
_ARTIST_WRAPPING_BRACKETS = {"(": ")", "[": "]", "【": "】", "（": "）", "［": "］"}


def _fold_char(ch: str) -> str:
    """Strip accents off a single character via NFKD decomposition, falling
    back to the original character when decomposition doesn't yield exactly
    one base character - keeps a 1:1 length correspondence with the input,
    which strip_artist_from_title() relies on to map a match back to
    original-string positions. "ł" / "ø", which have no decomposition, are
    folded with the same table as the search's _fold_diacritics(), so "Łona"
    is "Lona" here too.
    """
    decomposed = unicodedata.normalize("NFKD", ch)
    base = "".join(c for c in decomposed if not unicodedata.combining(c))
    return (base if len(base) == 1 else ch).translate(NON_DECOMPOSING_LETTER_FOLDS)


def strip_artist_from_title(title: str, artist: str) -> str:
    """If the song title itself repeats the artist's name, drop that
    occurrence - real case found testing this: channel "SIAMES", video
    titled 'SIAMÉS "Summer Nights" [Official Animated Video]' (the bare
    video title already includes the artist name, redundant once it's
    already stored separately as the song's artist).

    Diacritic-insensitive on both sides (via _fold_char - same technique as
    manage_youtube_playlists.py's _fold_diacritics, done per-character here
    to keep a position mapping back to the original title) since the
    channel name and the same name embedded in a title aren't always spelled
    identically, as in the SIAMES/SIAMÉS case above. Only removes a whole
    word/phrase match (not a substring of some other word), and leaves the
    title untouched entirely if that would empty it out.
    """
    if not title or not artist:
        return title

    folded_title = "".join(_fold_char(c) for c in title)
    folded_artist = "".join(_fold_char(c) for c in artist).strip()
    if not folded_artist:
        return title

    match = re.search(r"\b" + re.escape(folded_artist) + r"\b", folded_title, re.IGNORECASE)
    if not match:
        return title

    start, end = match.start(), match.end()
    if (
        start > 0
        and title[start - 1] in _ARTIST_WRAPPING_BRACKETS
        and end < len(title)
        and title[end] == _ARTIST_WRAPPING_BRACKETS[title[start - 1]]
    ):
        start -= 1
        end += 1

    remainder = (title[:start] + title[end:]).strip()

    # If the artist name was itself quoted (real case: 'SIAMÉS "Summer
    # Nights" [...]' - the quotes wrap the title, not the artist), removing
    # just the name leaves one quote character orphaned at the front with
    # its matching partner stranded mid-string rather than at an edge a
    # plain edge-trim would catch. Unwrap that pair specifically instead.
    if remainder[:1] in ('"', "'"):
        quote_char = remainder[0]
        closing = remainder.find(quote_char, 1)
        if closing != -1:
            remainder = remainder[1:closing] + remainder[closing + 1:]

    # Clean up whatever else was only there to connect the artist name to
    # the rest of the title - a leading "-"/":"/"–" and surrounding spaces.
    remainder = re.sub(r'^[\s\-:–—]+', "", remainder)
    remainder = re.sub(r'[\s\-:–—]+$', "", remainder)
    return remainder.strip() or title


def extract_playlist_id(raw: str) -> str | None:
    """Pull a playlist ID out of a pasted YouTube URL, or accept a bare ID
    typed directly. Handles both a dedicated playlist URL
    (.../playlist?list=PL...) and a "watch" URL that also carries a list=
    query param (.../watch?v=...&list=PL...).
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    match = re.search(r"[?&]list=([\w-]+)", raw)
    if match:
        return match.group(1)

    if re.fullmatch(r"[\w-]+", raw):
        return raw

    return None


def _fetch_playlist_items_ytdlp(playlist_id: str, timeout: int = 180) -> list | None:
    """Fetch a playlist's videos via yt-dlp - no OAuth, no API quota, works
    for anything reachable anonymously (public or unlisted playlists).

    Returns None (never []) when yt-dlp couldn't attempt this at all (binary
    missing, timed out, or produced no output whatsoever - which is also
    what a genuinely Private playlist looks like), so the caller can tell
    that apart from "fetched successfully, playlist just happens to be
    empty" and fall back to the Data API.

    Uses --flat-playlist: a plain --dump-json fetches full metadata for
    every video individually (one extra request each, ~1-2s/video observed),
    which is fine for a handful of videos but effectively hangs (well past
    any reasonable timeout) on a large playlist - a real 441-video playlist
    took over 13 minutes projected. --flat-playlist reads the playlist page
    itself only, still exposing id/title/channel (verified against a real
    playlist - channel/uploader are populated same as the non-flat form),
    which is everything build_metadata_from_item() needs.
    """
    url = f"https://www.youtube.com/playlist?list={playlist_id}"
    try:
        result = run_ytdlp(
            [
                "--dump-json", "--flat-playlist", "--ignore-errors", "--no-warnings",
                "--extractor-args", f"youtube:lang={YTDLP_PLAYLIST_LANG}", url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  ✖ yt-dlp unavailable ({e})")
        flog(f"[yt-dlp] playlist {playlist_id} unavailable: {e}")
        return None

    if not result.stdout.strip():
        flog(f"[yt-dlp] playlist {playlist_id} gave no videos (private?) exit={result.returncode}: "
             f"{ytdlp_errors(result) or 'no error'}")
        return None

    items = []
    for line in result.stdout.strip().splitlines():
        try:
            info = json.loads(line)
        except json.JSONDecodeError:
            continue
        video_id = info.get("id")
        if not video_id or is_placeholder_title(info.get("title")):
            continue
        items.append({
            "video_id": video_id,
            "title": info.get("title") or "",
            "channel": info.get("channel") or info.get("uploader") or "",
            "duration": info.get("duration"),
        })
    return items


def _fetch_playlist_items_api(playlist_id: str) -> list:
    """Fetch a playlist's videos via the YouTube Data API (OAuth, as the
    signed-in account) - the only way to reach a playlist that's genuinely
    Private (not just unlisted) rather than merely unreachable anonymously.
    """
    youtube = get_youtube_service()
    items = []
    page_token = None

    while True:
        response = youtube.playlistItems().list(
            part="snippet",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()

        for entry in response.get("items", []):
            snippet = entry.get("snippet", {})
            title = snippet.get("title", "")
            if is_placeholder_title(title):
                continue
            video_id = snippet.get("resourceId", {}).get("videoId")
            if not video_id:
                continue
            items.append({
                "video_id": video_id,
                "title": title,
                "channel": snippet.get("videoOwnerChannelTitle", ""),
            })

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    # playlistItems carries no length (yt-dlp's items do): asked for in batches,
    # one quota unit per 50 videos, so a full album is still told from a track
    durations = video_durations_via_api([item["video_id"] for item in items]) if items else {}
    if durations is None:
        flog(f"[YouTube API] no video lengths for playlist {playlist_id} - full albums go by title only")
        durations = {}
    for item in items:
        item["duration"] = durations.get(item["video_id"])
    return items


def get_playlist_items(playlist_input: str) -> list:
    """Resolve a pasted playlist URL/ID to its list of {video_id, title,
    channel} videos.

    Prefers yt-dlp (no auth, no quota); only falls back to the Data API
    (OAuth) when yt-dlp can't reach the playlist at all, e.g. because it's
    Private rather than just unlisted.
    """
    playlist_id = extract_playlist_id(playlist_input)
    if not playlist_id:
        print(f"Couldn't find a playlist ID in '{playlist_input}'")
        return []

    print(f"Fetching playlist {playlist_id} via yt-dlp...")
    items = _fetch_playlist_items_ytdlp(playlist_id)
    if items is not None:
        print(f"  ✔ Found {len(items)} video(s) via yt-dlp")
        return items

    print("yt-dlp couldn't fetch this playlist (likely private) - trying the YouTube API...")
    try:
        items = _fetch_playlist_items_api(playlist_id)
    except HttpError as e:
        print(f"  ✖ Failed to fetch playlist via API: {e}")
        return []
    except YouTubeLoginError as e:
        print(f"  ✖ Failed to fetch playlist via API: {e}")
        return []

    print(f"  ✔ Found {len(items)} video(s) via API")
    return items


# Channels whose video titles don't follow any "Artist - Title" convention
# at all - e.g. a TV/film soundtrack-compilation channel that tags each
# episode's featured song inside a sentence buried in the title text itself
# (real case: "Sex Education SoundTrack | S02E08 Care by Ezra Furman", where
# the real artist/title are "Ezra Furman"/"Care"). build_metadata_from_item's
# usual splitting can't make sense of these at all, so they're resolved
# separately by resolve_special_channel_metadata() before it ever runs.
# Matched case-insensitively against the exact channel name.
SPECIAL_DESCRIPTION_CHANNELS = {"soundtrack series", "aesthetic waves", "soundtracksost", "lakeshore records"}


def _is_special_description_channel(channel: str) -> bool:
    return (channel or "").strip().lower() in SPECIAL_DESCRIPTION_CHANNELS


# Words marking a genuinely different performance/version of a song - see
# YOUTUBE_TITLE_JUNK_PHRASES above for why these are deliberately never
# treated as junk. _find_official_match_via_search() skips its search
# entirely when one of these appears in the video's own title, since
# blindly trusting a same-named studio release there would silently swap
# out the very distinction the title is making.
_VERSION_MARKER_RE = re.compile(
    r"\b(?:Live|Cover|Remix|Acoustic|Extended|Club Edit)\b", re.IGNORECASE
)


def _contains_whole_word(folded_haystack: str, needle: str) -> bool:
    """Diacritic/case-insensitive whole-word containment check - same
    approach as strip_artist_from_title() above, reused here to confirm a
    search result's own artist/track genuinely appear in the source title
    rather than being an unrelated same-named release."""
    folded_needle = "".join(_fold_char(c) for c in needle).strip()
    if not folded_needle:
        return False
    return bool(re.search(r"\b" + re.escape(folded_needle) + r"\b", folded_haystack, re.IGNORECASE))


def _find_official_match_via_search(raw_title: str, max_results: int = 5) -> tuple | None:
    """Search YouTube for `raw_title` verbatim (yt-dlp's own ranking already
    surfaces the right official upload near the top even for a messy title
    like the SPECIAL_DESCRIPTION_CHANNELS case above - verified directly)
    and, if one of the top results is a confirmed official release (see
    _is_official_release()), return its clean (artist, title) straight from
    yt-dlp's own metadata - not anything guessed from the messy source
    title or its description.

    Guards against a same-named but unrelated official track winning by
    requiring the candidate's own artist and track to actually appear,
    whole-word, in `raw_title` (_contains_whole_word()) before trusting it.
    Returns None if nothing both official and confirmed turns up, or if
    `raw_title` carries a real-version marker (_VERSION_MARKER_RE) - in that
    case the caller should fall back to a source that can't overwrite the
    version distinction the title is making.
    """
    if _VERSION_MARKER_RE.search(raw_title or ""):
        return None

    result = _run_ytdlp_search(raw_title, max_results)
    if result is None:
        return None
    # Judged by stdout, not the exit code: yt-dlp exits 1 when one hit is
    # unavailable, with the other hits still printed (as _search_video_ytdlp_once())
    _log_ytdlp_errors(raw_title, result)
    if not result.stdout.strip():
        return None

    folded_raw_title = "".join(_fold_char(c) for c in raw_title)

    for line in result.stdout.strip().splitlines():
        try:
            info = json.loads(line)
        except json.JSONDecodeError:
            continue
        channel = info.get("channel") or info.get("uploader") or ""
        track = info.get("track")
        artist = info.get("artist")
        if not (track and artist and _is_official_release(channel, track)):
            continue
        if _contains_whole_word(folded_raw_title, track) and _contains_whole_word(folded_raw_title, artist):
            return artist, track

    return None


def _fetch_video_description(video_id: str, timeout: int = 30) -> str | None:
    """Fetch one video's full description via yt-dlp - only needed as a
    fallback for SPECIAL_DESCRIPTION_CHANNELS videos where
    _find_official_match_via_search() found nothing (e.g. the song was never
    officially distributed to YouTube under its own upload), since
    --flat-playlist (the normal playlist fetch) never includes it."""
    info = fetch_video_info(video_id, timeout)
    return info.get("description") if info else None


def fetch_video_info(video_id: str, timeout: int = 30) -> dict | None:
    """One video's yt-dlp metadata (title, channel, description, ...), None
    when it can't be had (the reason in debug.log)."""
    if not video_id:
        return None
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        result = run_ytdlp(
            ["--dump-json", "--no-warnings", "--skip-download", url],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        flog(f"[yt-dlp] no data for video {video_id}: {e}")
        return None
    if result.returncode != 0 or not result.stdout.strip():
        flog(f"[yt-dlp] no data for video {video_id} exit={result.returncode}: "
             f"{ytdlp_errors(result) or 'no output'}")
        return None
    try:
        return json.loads(result.stdout.strip().splitlines()[0])
    except (json.JSONDecodeError, IndexError) as e:
        flog(f"[yt-dlp] unreadable data for video {video_id}: {e}")
        return None


# Matches a "Music : <title> by <artist>" line the way SPECIAL_DESCRIPTION_
# CHANNELS' videos credit a soundtrack cue's real artist/title in their
# description (real case: "Music : Care by Ezra Furman").
_MUSIC_DESCRIPTION_LINE_RE = re.compile(r"(?im)^\s*music\s*:\s*(.+?)\s+by\s+(.+?)\s*$")


def _parse_music_description_line(description: str) -> tuple | None:
    """Pull (artist, title) out of a "Music : <title> by <artist>" line in
    `description` - see _MUSIC_DESCRIPTION_LINE_RE above."""
    match = _MUSIC_DESCRIPTION_LINE_RE.search(description or "")
    if not match:
        return None
    title, artist = match.group(1).strip(), match.group(2).strip()
    return (artist, title) if title and artist else None


# Matches a quoted "<title>" by <artist> credit embedded directly in a
# video's own title rather than its description (real case, "aesthetic
# waves": 'Umbrella Academy Season 4 Episode 1 - OST: "Santa Baby" by Eartha
# Kitt' -> title "Santa Baby", artist "Eartha Kitt"). Reuses
# _DOUBLE_QUOTE_RE's character class so curly/CJK quote variants match too.
# Not anchored to the start of the string, since these channels routinely
# prefix the quoted credit with an episode/segment label. The artist is
# taken as the rest of the string after " by ", so this only fires on the
# *last* such quoted credit in the title - see _parse_quoted_title_by_artist()
# for the extra trim this can need when that "rest" carries more than just
# the artist name.
_TITLE_QUOTED_BY_RE = re.compile(
    r'["“”„‟『』]([^"“”„‟『』]+)["“”„‟『』]\s+by\s+(.+?)\s*$', re.IGNORECASE
)

# Some channels (real case, "Lakeshore Records": "CYBERPUNK 2077 - KILL THE
# MESSENGER by Rezodrone (Jason Charles Miller & Jamison Boaz)") credit a
# track the same "<title> by <artist>" way but without quoting the title at
# all, instead separating it from a leading game/show name with " - ".
# Tried only when the quoted form above doesn't match, since a quoted title
# is the more reliable signal when both are present.
_DASH_TITLE_BY_RE = re.compile(r"-\s*(.+?)\s+by\s+(.+?)\s*$", re.IGNORECASE)

# Some channels (real case, "SoundtracksOST": '"Cyberwildlife Park" by Marcin
# Przybylowicz - Cyberpunk: Edgerunners [OST]') tack an album/soundtrack
# credit onto the artist portion after " by ", separated by " - ", the same
# "Artist - <something>" convention split_artist_title() relies on elsewhere.
# Only the part before the first " - " is the actual artist name.
_ARTIST_ALBUM_SUFFIX_RE = re.compile(r"\s+-\s+.+$")


def _parse_quoted_title_by_artist(title: str) -> tuple | None:
    """Pull (artist, title) out of a '"<title>" by <artist>' credit in a raw
    video title - see _TITLE_QUOTED_BY_RE above - or, failing that, an
    unquoted "<prefix> - <title> by <artist>" credit - see _DASH_TITLE_BY_RE
    above. Also trims a trailing " - <album/soundtrack name>" some channels
    append after the artist name itself - see _ARTIST_ALBUM_SUFFIX_RE above."""
    match = _TITLE_QUOTED_BY_RE.search(title or "") or _DASH_TITLE_BY_RE.search(title or "")
    if not match:
        return None
    song_title = match.group(1).strip()
    artist = _ARTIST_ALBUM_SUFFIX_RE.sub("", match.group(2).strip()).strip()
    return (artist, song_title) if song_title and artist else None


def resolve_special_channel_metadata(item: dict) -> tuple | None:
    """For a video from a SPECIAL_DESCRIPTION_CHANNELS channel, resolve the
    real (artist, title), in order:

    1. _find_official_match_via_search() - search YouTube for the raw video
       title verbatim and take the clean artist/track off a confirmed
       official release among the top results.
    2. _parse_quoted_title_by_artist() - fall back to a '"<title>" by
       <artist>' or unquoted "<prefix> - <title> by <artist>" credit embedded
       directly in the video's own title, for a channel that credits songs
       that way (e.g. "aesthetic waves", "lakeshore records"). Cheap (no
       extra request) but unverified, unlike step 1.
    3. _parse_music_description_line() - fall back further to the video's
       own description, for a song that was never officially distributed to
       YouTube under its own upload and isn't credited in the title either.

    Returns None if none of these work, so the caller falls back to
    build_metadata_from_item()'s normal channel/title-based parsing.
    """
    title = item.get("title") or ""
    found = _find_official_match_via_search(title)
    if found:
        show(f"{styled(item.get('channel'), 'green')} special case: matched official release {styled(f'{found[0]} - {found[1]}', 'blue')} for \"{styled(title, 'yellow')}\"")
        return found

    found = _parse_quoted_title_by_artist(title)
    if found:
        show(f"{styled(item.get('channel'), 'green')} special case: parsed {styled(f'{found[0]} - {found[1]}', 'blue')} from title \"{styled(title, 'yellow')}\"")
        return found

    description = _fetch_video_description(item.get("video_id"))
    found = _parse_music_description_line(description) if description else None
    if found:
        show(f"{styled(item.get('channel'), 'green')} special case: parsed {styled(f'{found[0]} - {found[1]}', 'blue')} from description for \"{styled(title, 'yellow')}\"")
    return found


# "<Name> - Topic" channels whose name is NOT an artist: YouTube files an
# auto-generated upload under one of these when it couldn't attach the
# track to a real artist channel (real case: "Release - Topic" carrying
# "Mind Me" by Oordaya and "Slide" by Frankie Cosmos). The video title is
# still the bare song title, but the channel name is useless as the artist,
# so _artist_from_topic_description() reads it off the description instead.
# Matched case-insensitively against the channel name minus " - Topic".
GENERIC_TOPIC_CHANNELS = {"release", "various artists"}


def _is_generic_topic_channel(channel: str) -> bool:
    if not (channel and _is_topic_channel(channel)):
        return False
    base = re.sub(r"\s*-\s*topic$", "", channel, flags=re.IGNORECASE).strip().lower()
    return base in GENERIC_TOPIC_CHANNELS


# An auto-generated ("Provided to YouTube by ...") description credits the
# track on one line as "<Title> · <Artist> · <Artist 2> ...", and some also
# carry a separate "Main Artist: <Artist>" line further down.
_TOPIC_CREDIT_SEPARATOR = " · "
_MAIN_ARTIST_LINE_RE = re.compile(r"(?im)^\s*main artist\s*:\s*(.+?)\s*$")


def topic_description_credits(description: str) -> tuple[str, list[str]] | None:
    """(title, [artists]) from the first "<Title> · <Artist> · <Artist 2>"
    line of an auto-generated Topic video description, the artists in the
    description's order (not necessarily the main one first: "Śniadanie ·
    Natalia Przybysz · Fisz Emade Tworzywo"). None without such a line."""
    for line in (description or "").splitlines():
        parts = [p.strip() for p in line.split(_TOPIC_CREDIT_SEPARATOR)]
        if len(parts) >= 2 and all(parts):
            return parts[0], parts[1:]
    return None


def _parse_topic_description_artist(description: str) -> str | None:
    """Pull the artist out of an auto-generated Topic video description -
    the artists after the title on the first "<Title> · <Artist>" line
    (several joined with ", ", left for the artist-split review to break
    up), else every "Main Artist:" line. None if neither is there."""
    credits = topic_description_credits(description)
    if credits:
        return ", ".join(credits[1])
    main_artists = [a for a in _MAIN_ARTIST_LINE_RE.findall(description or "") if a]
    return ", ".join(main_artists) or None


def resolve_generic_topic_artist(item: dict) -> str | None:
    """For a GENERIC_TOPIC_CHANNELS video, fetch its description and return
    the real artist credited there, or None if it can't be found - the
    entry then goes to _review_missing_artists() for the user to fill in."""
    description = _fetch_video_description(item.get("video_id"))
    artist = _parse_topic_description_artist(description) if description else None
    if artist:
        show(f"{styled(item.get('channel'), 'green')} is not an artist: found {styled(artist, 'blue')} in the description of \"{styled(item.get('title'), 'yellow')}\"")
    return artist


def build_metadata_from_item(item: dict, artist_title_override: tuple | None = None) -> dict:
    """Turn one playlist video into an import_engine metadata dict.

    A "<Artist> - Topic" channel's video title is deliberately just the bare
    song title with no artist in it at all (see _is_topic_channel's
    docstring) - splitting that on " - " would misparse it, so the channel
    name (minus the "- Topic" suffix) is used as the artist directly
    instead. Every other video first tries splitting "Artist - Title" out of
    the title text itself, the same convention extract_unknown_data() already
    relies on for mp3 filenames - but real playlists routinely have plain
    artist-channel uploads (not auto-generated "- Topic" channels) whose
    title is *also* just the bare song title with no separator at all (real
    case found testing this: Culture Beat's own channel, video titled just
    "Mr. Vain (Original Radio Edit)"). When splitting finds no separator,
    fall back to the channel name as the artist there too - same idea as the
    Topic-channel case, just for channels that don't follow that naming
    convention.

    `artist_title_override` (from resolve_special_channel_metadata()) skips
    all of the above entirely - for a SPECIAL_DESCRIPTION_CHANNELS video the
    title text itself carries no "Artist - Title" structure to split at all.
    """
    channel = item.get("channel") or ""
    title = item.get("title") or ""

    if artist_title_override:
        artist, song_title = artist_title_override
    else:
        parse_title = strip_pipe_suffix(title)
        parse_title = strip_junk_brackets_anywhere(parse_title)
        parse_title = strip_hashtags(parse_title)
        parse_title = _MEDIA_EXTENSION_RE.sub("", parse_title).strip()

        if _is_generic_topic_channel(channel):
            # Channel name isn't an artist - left empty for the description
            # lookup / _review_missing_artists() to fill in.
            artist = None
            song_title = parse_title.strip()
        elif channel and _is_topic_channel(channel):
            artist = re.sub(r"\s*-\s*topic$", "", channel, flags=re.IGNORECASE).strip()
            song_title = parse_title.strip()
        elif quoted_credit := split_quoted_title_feat_credit(parse_title):
            artist, song_title = quoted_credit
        else:
            artist, song_title = split_artist_title(parse_title)
            if (channel.strip() and artist and is_junk_only_title(song_title)
                    and name_key(artist) != name_key(channel)):
                # "Glow - Lyric Video": the title on the left, the artist only the channel
                artist, song_title = channel.strip(), artist
            elif not artist or not song_title:
                corner_credit = split_corner_bracket_credit(parse_title)
                if corner_credit:
                    artist, song_title = corner_credit
                elif channel:
                    artist = channel.strip()
                    song_title = parse_title.strip()

    if song_title and artist:
        song_title = strip_artist_from_title(song_title, artist)

    if song_title:
        song_title = strip_junk_suffix(song_title)
        # Unbracketed trailing junk too (real case: '“Golden” Official Lyric
        # Video | KPop Demon Hunters | Sony Animation'), unless that would
        # leave nothing - then the phrase *is* the title.
        song_title = strip_trailing_junk_suffix(song_title) or song_title
        song_title = strip_trailing_dash_year(song_title)
        song_title = strip_junk_suffix(song_title)
        song_title = strip_quote_marks(song_title)
        song_title = clean_file_name_title(song_title)
    if artist:
        artist = clean_file_name_title(artist)

    return {
        "artist_name": artist,
        "title": song_title,
        "album": None,
        "year": None,
        "language": "Unknown",
        "origin": None,
        "youtube_video_id": item.get("video_id"),
        # Tells a full-album video from a track (song_kinds.songs_for_entry()); flat-playlist only
        "duration_s": item.get("duration"),
        "_label": title or item.get("video_id"),
    }


# Some channels display a video's title as "<Song title> - <Artist>" rather
# than the usual "<Artist> - <Song title>" (real case: "The Darkness That
# You Fear - The Chemical Brothers"), which split_artist_title() has no way
# to tell apart from the normal order - it always parses the left side as
# the artist. _review_artist_title_swaps() below catches this after the
# fact by checking whether the *parsed title* matches an artist already in
# the database, and lets the user decide what to do with each match.
_SWAP_CHOICE_ADD = "Add"
_SWAP_CHOICE_SWAP = "Swap title with artist and add"
_SWAP_CHOICE_MANUAL = "Add manually"
_SWAP_CHOICE_SKIP = "Don't add"


def _find_possible_artist_title_swaps(metadata_list: list) -> list:
    """Flag entries whose parsed *title* matches an artist already in the
    database - a sign the title and artist were parsed backwards.

    An entry is skipped when its parsed *artist* also already matches an
    existing artist (e.g. "CHVRCHES - Nightmares", where the title happens
    to fuzzy-match "Nightmares on Wax") - the current parse is already
    plausible, so it's not worth asking the user about a swap.

    Pure lookup - does NOT ask the user or modify metadata_list. Returns a
    list of (metadata, matched_artist) pairs for the caller to act on.
    """
    flagged = []
    for metadata in metadata_list:
        title = metadata.get("title")
        if not title:
            continue
        artist_name = metadata.get("artist_name")
        if artist_name and find_matching_artist(artist_name):
            continue
        matched_artist = find_matching_artist(title)
        if matched_artist:
            flagged.append((metadata, matched_artist))
    return flagged


def _review_artist_title_swaps(flagged: list) -> tuple:
    """Ask the user, one by one, what to do with each entry flagged by
    _find_possible_artist_title_swaps(): add as parsed, swap artist/title
    and add, or drop it entirely - instead of silently importing a song
    under the wrong artist.

    Returns (resolved_metadata_list, pre_skipped): the flagged entries the
    user chose to add (as-is or swapped), and (label, reason) tuples for the
    ones they dropped - same shape as run_import_batch()'s pre_skipped.
    """
    if not flagged:
        return [], []

    remaining = []
    pre_skipped = []

    review_header(f"REVIEW: {len(flagged)} song(s) may have their artist and title swapped")
    for metadata, matched_artist in flagged:
        # The parsed (cleaned) "artist - title" is what was actually compared
        # against the database, so it's shown as the main label; the raw
        # video title is kept alongside it (a different color, in quotes)
        # purely as context for which video this came from.
        label = f"{metadata['artist_name']} - {metadata['title']}"
        raw_label = metadata.get("_label") or label
        show(f"{styled(label, 'green')} (raw: \"{styled(raw_label, 'yellow')}\") - the title looks like it could be an artist: {styled(matched_artist.name, 'blue')} is already in the database")
        choice = questionary.select(
            "What do you want to do with this song?",
            choices=[_SWAP_CHOICE_ADD, _SWAP_CHOICE_SWAP, _SWAP_CHOICE_MANUAL, _SWAP_CHOICE_SKIP],
        ).ask()

        if choice == _SWAP_CHOICE_SWAP:
            metadata["artist_name"], metadata["title"] = metadata["title"], metadata["artist_name"]
            remaining.append(metadata)
        elif choice == _SWAP_CHOICE_MANUAL:
            artist = (questionary.text("Artist:").ask() or "").strip()
            title = (questionary.text("Title:").ask() or "").strip() if artist else ""
            if not (artist and title):  # Ctrl+C or nothing typed: skipped, like _review_missing_artists()
                pre_skipped.append((label, "Possible artist/title swap - nothing typed, not added"))
            else:
                metadata["artist_name"], metadata["title"] = artist, title
                remaining.append(metadata)
        elif choice == _SWAP_CHOICE_SKIP:
            pre_skipped.append((label, "Possible artist/title swap - user chose not to add"))
        else:
            remaining.append(metadata)
        print()

    return remaining, pre_skipped


def _resolve_artist_synonyms(metadata_list: list) -> None:
    """Replace each entry's parsed artist name with its canonical DB name
    when the parsed name exactly matches a known synonym (the artists
    table's `synonyms` column) rather than the artist's own name - e.g. a
    channel handle like "akuteofficial" stored as a synonym of "Akute" gets
    swapped to "Akute" before anything is shown to the user.

    Runs before every user-facing display of the parsed entries (the
    artist/title swap review, and the per-entry prints during the import
    batch itself) so those already show the real artist name, instead of
    creating a separate "akuteofficial" artist entry that happens to share
    all its songs with the real one.

    Matching is an exact, case-insensitive comparison against a known
    synonym - deliberately not fuzzy, since this silently rewrites what the
    user sees rather than just flagging it for review like the artist/title
    swap check does. Mutates metadata_list's entries in place.
    """
    synonym_to_canonical = {}
    for artist in get_artists_from_db_session():
        for synonym in _parse_synonyms(artist_match_synonyms(artist)):
            synonym_to_canonical[synonym.strip().lower()] = artist.name

    if not synonym_to_canonical:
        return

    for metadata in metadata_list:
        artist_name = metadata.get("artist_name")
        if not artist_name:
            continue
        canonical_name = synonym_to_canonical.get(artist_name.strip().lower())
        if canonical_name and canonical_name != artist_name:
            show(f"Artist synonym match: {styled(artist_name, 'yellow')} is known as {styled(canonical_name, 'blue')}")
            metadata["artist_name"] = canonical_name


def _review_missing_artists(metadata_list: list) -> tuple:
    """Ask the user, once the whole playlist has been scanned, for the
    artist of every entry that still has none (e.g. a GENERIC_TOPIC_CHANNELS
    video whose description credits nobody) - an empty answer skips it.

    Returns (remaining_metadata_list, pre_skipped), same shape as
    _review_artist_title_swaps().
    """
    missing = [m for m in metadata_list if not (m.get("artist_name") or "").strip()]
    if not missing:
        return metadata_list, []

    missing_ids = {id(m) for m in missing}
    remaining = [m for m in metadata_list if id(m) not in missing_ids]
    pre_skipped = []

    review_header(f"REVIEW: {len(missing)} song(s) have no artist - enter it manually")
    for metadata in missing:
        label = metadata.get("_label") or metadata.get("title") or ""
        video_id = metadata.get("youtube_video_id")
        link = f" https://youtu.be/{video_id}" if video_id else ""
        show(f"Title: {styled(metadata.get('title'), 'green')} (raw: \"{styled(label, 'yellow')}\"){link}")
        artist = (questionary.text("Artist (leave empty to skip this song):").ask() or "").strip()
        if artist:
            metadata["artist_name"] = artist
            remaining.append(metadata)
        else:
            pre_skipped.append((label, "No artist found - user didn't enter one"))
        print()

    return remaining, pre_skipped


def playlist_item_metadata(item: dict) -> dict:
    """build_metadata_from_item() for one playlist video, with the artist
    of a SPECIAL_DESCRIPTION_CHANNELS / generic "Release - Topic" upload read
    from its description (one extra request for those)."""
    channel = item.get("channel") or ""
    override = resolve_special_channel_metadata(item) if _is_special_description_channel(channel) else None
    metadata = build_metadata_from_item(item, override)
    if not metadata["artist_name"] and _is_generic_topic_channel(channel):
        metadata["artist_name"] = resolve_generic_topic_artist(item)
    return metadata


def import_data_from_youtube_playlist(playlist_input: str) -> list:
    items = get_playlist_items(playlist_input)
    if not items:
        print("No videos found in this playlist.")
        return []

    metadata_list = [playlist_item_metadata(item) for item in items]

    _resolve_artist_synonyms(metadata_list)

    # Entries that need the user (no artist at all, or a possible
    # artist/title swap) are held back and only asked about once
    # run_import_batch() has got through the slow part with everything else,
    # so the user isn't kept waiting for the import to start.
    missing_artist = [m for m in metadata_list if not (m.get("artist_name") or "").strip()]
    rest = [m for m in metadata_list if (m.get("artist_name") or "").strip()]
    flagged = _find_possible_artist_title_swaps(rest)
    flagged_ids = {id(metadata) for metadata, _ in flagged}
    rest = [m for m in rest if id(m) not in flagged_ids]

    def review_held_back_entries() -> tuple:
        filled_in, missing_skipped = _review_missing_artists(missing_artist)
        _resolve_artist_synonyms(filled_in)
        swapped, swap_skipped = _review_artist_title_swaps(flagged)
        # A swapped title, or an artist typed by hand, can be a synonym too
        _resolve_artist_synonyms(swapped)
        return filled_in + swapped, missing_skipped + swap_skipped

    return run_import_batch(rest, late_entries=review_held_back_entries)
