import requests
from typing import Optional

def get_album_name(artist, title):
    print("WORK IN PROGRESS")
    return None

def get_album_itunes(artist: str, title: str) -> Optional[str]:
    params = {
        "term": f"{artist} {title}",
        "media": "music",
        "entity": "song",
        "limit": 10
    }
    
    response = requests.get("https://itunes.apple.com/search", params=params)
    response.raise_for_status()
    
    results = response.json().get("results", [])
    
    for track in results:
        # Verify artist matches to avoid wrong results
        if artist.lower() in track.get("artistName", "").lower():
            album = track.get("collectionName")
            if album:
                return album
    
    return None