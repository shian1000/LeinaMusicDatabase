import os
import time
from typing import List, Dict

import questionary
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from src.yt_cache import make_song_key, save_cache
from src.yt_cache import load_cache, init_cache, clear_cache
from src.yt_cache import save_cache
from src.yt_cache import make_song_key
import json



SCOPES = ["https://www.googleapis.com/auth/youtube"]

TOKEN_PATH = ".secrets/token.json"
CLIENT_SECRET_PATH = ".secrets/client_secret.json"


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

def search_video(youtube, artist: str, title: str) -> str | None:
    query = f"{artist} - {title}"
    print(f"\nSearching for: {query}")

    try:
        response = youtube.search().list(
            part="snippet",
            q=query,
            type="video",
            maxResults=1
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

    video_id = items[0]["id"]["videoId"]
    print(f"  ✔ Found video: {video_id}")
    return video_id


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


# ---------------------------------------------------------
# MAIN FUNCTION
# ---------------------------------------------------------

# def create_yt_playlist(song_list: List[Dict[str, str]], playlist_name: str):
#     """
#     Create a YouTube playlist from a list of:
#     { "title": "...", "artist": "..." }
#     """

#     youtube = get_youtube_service()

#     playlist_id = create_playlist(
#         youtube,
#         title=playlist_name,
#         description="Generated automatically from database"
#     )

#     for song in song_list:
#         title = song.title
#         artist = song.artist.name

#         video_id = search_video_cached(youtube, artist, title)
#         if video_id:
#             add_video_to_playlist(youtube, playlist_id, video_id)

#     print("\n🎉 Playlist creation complete!")

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

            print(f"\nSearching for: {artist} - {title}")

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
    print("\n🎉 Playlist creation complete!")
    clear_cache()





# ---------------------------------------------------------
# For testing
# ---------------------------------------------------------

if __name__ == "__main__":
    test_songs = [
        {"title": "Hey Jude", "artist": "The Beatles"},
        {"title": "Rolling in the Deep", "artist": "Adele"}
    ]

    create_yt_playlist(test_songs)
