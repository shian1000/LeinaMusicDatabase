import os
import shutil
import subprocess
import sys
import time
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
import json


import json
from googleapiclient.errors import HttpError


SCOPES = ["https://www.googleapis.com/auth/youtube"]

TOKEN_PATH = ".secrets/token.json"
CLIENT_SECRET_PATH = ".secrets/client_secret.json"

HQ_KEYWORDS = ["hq", "hd", "high quality", "official audio", "audio", "remaster", "flac", "320"]
# Keywords that suggest we should deprioritize (lower = worse)
LQ_KEYWORDS = ["live", "concert", "tour", "performance", "session", "acoustic", "cover", "karaoke", "instrumental", "remix"]
VIDEO_KEYWORDS = ["official video", "music video", "mv", "official mv", "video clip"]



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

def score_result(title: str) -> int:
    title_lower = title.lower()
    score = 0
    for kw in HQ_KEYWORDS:
        if kw in title_lower:
            score += 2
    for kw in VIDEO_KEYWORDS:
        if kw in title_lower:
            score -= 2
    for kw in LQ_KEYWORDS:
        if kw in title_lower:
            score -= 1
    return score

def search_video_ytdlp(artist: str, title: str, max_results: int = 5) -> str | None:
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
            if video_id:
                candidates.append((score_result(video_title), video_id, video_title))
        except json.JSONDecodeError:
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_id, best_title = candidates[0]
    print(f"  ✔ Best match (score={best_score}): {best_title} [{best_id}]")
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
            maxResults=5,
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
        (score_result(item["snippet"]["title"]), item["id"]["videoId"], item["snippet"]["title"])
        for item in items
    ]
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_id, best_title = candidates[0]
    print(f"  ✔ Best match (score={best_score}): {best_title} [{best_id}]")
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

