import os
import re
import shutil
import subprocess
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
import json


import json
from googleapiclient.errors import HttpError


SCOPES = ["https://www.googleapis.com/auth/youtube"]

TOKEN_PATH = ".secrets/token.json"
CLIENT_SECRET_PATH = ".secrets/client_secret.json"

HQ_KEYWORDS = ["hq", "hd", "high quality", "official audio", "audio", "remaster", "flac", "320"]
# Keywords that suggest we should deprioritize (lower = worse)
LQ_KEYWORDS = ["live", "concert", "tour", "performance", "session", "acoustic", "cover", "karaoke", "instrumental", "remix", "sped up", "slowed", "nightcore", "8d audio", "bass boosted"]
VIDEO_KEYWORDS = ["official video", "music video", "mv", "official mv", "video clip"]

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
            maxResults=1
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

def _relevance_text(text: str) -> str:
    """Strip bracketed annotations and hashtags before comparing titles.

    DB titles/video titles often carry junk like "[Official Video]" or
    "[Save Ukraine - #StopWar]" that isn't part of the song's identity. If
    two unrelated videos both happen to carry the same bracketed tag, a
    plain similarity check on the raw strings would rate them as similar
    for the wrong reason, so that text is dropped before comparing.
    """
    text = remove_brackets(text)
    text = re.sub(r"#\w+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _title_containment(expected: str, candidate: str) -> float:
    """How much of `expected` shows up intact inside `candidate`.

    A plain whole-string similarity() ratio unfairly penalizes a short,
    exact title buried in a longer, decorated candidate title (e.g. title
    "As" vs candidate "Gosia Kunc - As" scores only ~0.24 on similarity()
    alone, despite being an exact match) — the surrounding text dilutes the
    ratio. This checks containment directly instead.

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


def score_result(candidate_title: str, artist: str, title: str, channel: str = "") -> tuple[float, float, int]:
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
    """
    expected_title = _relevance_text(title)
    candidate_clean = _relevance_text(candidate_title)
    if expected_title and candidate_clean:
        relevance = max(
            similarity(expected_title, candidate_clean),
            _title_containment(expected_title, candidate_clean),
        )
    else:
        relevance = 0.0

    expected_artist = _relevance_text(artist)
    channel_clean = _relevance_text(channel)
    artist_relevance = 0.0
    if expected_artist and candidate_clean:
        artist_relevance = similarity(expected_artist, candidate_clean)
    if expected_artist and channel_clean:
        artist_relevance = max(artist_relevance, similarity(expected_artist, channel_clean))

    title_lower = candidate_title.lower()
    quality = 0
    for kw in HQ_KEYWORDS:
        if kw in title_lower:
            quality += 2
    for kw in VIDEO_KEYWORDS:
        if kw in title_lower:
            quality -= 2
    for kw in LQ_KEYWORDS:
        if kw in title_lower:
            quality -= 1
    if channel and _is_topic_channel(channel):
        quality += 2
    return relevance, artist_relevance, quality

def search_video_ytdlp(artist: str, title: str, max_results: int = 8) -> str | None:
    """Search YouTube using yt-dlp (no API quota used)."""
    query = f"{artist} - {title} audio"
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
            if video_id:
                candidates.append((score_result(video_title, artist, title, channel), video_id, video_title))
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
    query = f"{artist} - {title} audio"
    print(f"  Searching API for: {query}")

    try:
        response = youtube.search().list(
            part="snippet",
            q=query,
            type="video",
            maxResults=8,
            videoCategoryId="10",  # Music category
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

