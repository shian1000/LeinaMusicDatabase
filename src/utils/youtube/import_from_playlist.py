import json
import re
import subprocess
import unicodedata

from googleapiclient.errors import HttpError
from rich import print

from utils.youtube.manage_youtube_playlists import get_youtube_service, _is_topic_channel
from utils.common.normalizer import split_artist_title
from utils.discoveries.import_engine import run_import_batch

# What YouTube reports as the title for a playlist entry that's no longer
# reachable (removed by the uploader, or made private) - these carry no
# usable metadata at all and are skipped outright rather than fed into the
# import batch as "unknown artist/title" entries.
PLACEHOLDER_TITLES = {"Private video", "Deleted video"}

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
    "Video Clip",
    "Original Radio Edit",
    "Radio Edit",
    "Radio Version",
    "Album Version",
    "Original Mix",
    "Explicit",
    "Clean Version",
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
    "Official",
]
_YOUTUBE_TITLE_JUNK_MARKER_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in YOUTUBE_TITLE_JUNK_MARKER_WORDS) + r")\b",
    re.IGNORECASE,
)

# Matches one "(...)"/"[...]" group (no nested brackets) at the very end of
# the string, plus any trailing whitespace.
_TRAILING_BRACKET_RE = re.compile(r"\s*[\(\[]([^()\[\]]*)[\)\]]\s*$")


def strip_junk_suffix(title: str) -> str:
    """Repeatedly strip a trailing bracket group from `title` as long as it's
    junk - either its content matches YOUTUBE_TITLE_JUNK_PHRASES exactly, or
    it contains one of YOUTUBE_TITLE_JUNK_MARKER_WORDS anywhere - handles a
    title decorated with more than one such annotation (e.g. "Song (Official
    Video) (HD)"). Only ever touches the trailing bracket, never one earlier
    in the title (e.g. a genuine subtitle like "Sad Story (Out of Luck)" is
    left alone).
    """
    title = (title or "").strip()
    while True:
        match = _TRAILING_BRACKET_RE.search(title)
        if not match:
            break
        content = match.group(1).strip()
        is_exact_junk = content.lower() in _YOUTUBE_TITLE_JUNK_PHRASES_LOWER
        has_marker_word = bool(_YOUTUBE_TITLE_JUNK_MARKER_RE.search(content))
        if not (is_exact_junk or has_marker_word):
            break
        title = title[:match.start()].strip()
    return title


def _fold_char(ch: str) -> str:
    """Strip accents off a single character via NFKD decomposition, falling
    back to the original character when decomposition doesn't yield exactly
    one base character - keeps a 1:1 length correspondence with the input,
    which strip_artist_from_title() relies on to map a match back to
    original-string positions.
    """
    decomposed = unicodedata.normalize("NFKD", ch)
    base = "".join(c for c in decomposed if not unicodedata.combining(c))
    return base if len(base) == 1 else ch


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

    remainder = (title[:match.start()] + title[match.end():]).strip()

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
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--flat-playlist", "--ignore-errors", "--no-warnings", url],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  ✖ yt-dlp unavailable ({e})")
        return None

    if not result.stdout.strip():
        return None

    items = []
    for line in result.stdout.strip().splitlines():
        try:
            info = json.loads(line)
        except json.JSONDecodeError:
            continue
        video_id = info.get("id")
        if not video_id:
            continue
        items.append({
            "video_id": video_id,
            "title": info.get("title") or "",
            "channel": info.get("channel") or info.get("uploader") or "",
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
            if title in PLACEHOLDER_TITLES:
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

    print(f"  ✔ Found {len(items)} video(s) via API")
    return items


def build_metadata_from_item(item: dict) -> dict:
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
    """
    channel = item.get("channel") or ""
    title = item.get("title") or ""

    if channel and _is_topic_channel(channel):
        artist = re.sub(r"\s*-\s*topic$", "", channel, flags=re.IGNORECASE).strip()
        song_title = title.strip()
    else:
        artist, song_title = split_artist_title(title)
        if not artist or not song_title:
            if channel:
                artist = channel.strip()
                song_title = title.strip()

    if song_title and artist:
        song_title = strip_artist_from_title(song_title, artist)

    if song_title:
        song_title = strip_junk_suffix(song_title)

    return {
        "artist_name": artist,
        "title": song_title,
        "album": None,
        "year": None,
        "language": "Unknown",
        "origin": None,
        "youtube_video_id": item.get("video_id"),
        "_label": title or item.get("video_id"),
    }


def import_data_from_youtube_playlist(playlist_input: str) -> list:
    items = get_playlist_items(playlist_input)
    if not items:
        print("No videos found in this playlist.")
        return []

    # DEBUG: only process the first video of the playlist, ignore the rest.
    # Remove this line to go back to importing the whole playlist.
    items = items[:10]

    metadata_list = [build_metadata_from_item(item) for item in items]
    return run_import_batch(metadata_list)
