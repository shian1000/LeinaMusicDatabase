from bs4 import BeautifulSoup
from utils.selenium_sessions import get_global_driver
import time
from utils.debug import slog
from urllib.parse import quote
import json
from utils.text_utils import is_blacklisted_album

def extract_from_itunes_soup(song_soup, artist, song):
    # Parse the JSON-LD schema tag in <head>
    # slog(song_soup)
    schema_tag = song_soup.find("script", {"id": "schema:song", "type": "application/ld+json"})
    if not schema_tag:
        return None

    try:
        data = json.loads(schema_tag.string)
    except json.JSONDecodeError:
        return None

    audio = data.get("audio", {})

    # 1. Verify artist
    artists = audio.get("byArtist", [])
    artist_names = [a.get("name", "").lower() for a in artists]
    if not any(artist.lower() in name for name in artist_names):
        slog(f"Artist mismatch: expected '{artist}', found {artist_names}")
        return None

    # 2. Verify song title
    found_song = audio.get("name", "")
    if song.lower() not in found_song.lower():
        slog(f"Song mismatch: expected '{song}', found '{found_song}'")
        return None

    # 3. Extract album name
    album = audio.get("inAlbum", {}).get("name")
    if not album:
        return None

    slog(f"Matched: {artist} - {found_song} ({album})")
    return album

def get_album_name(artist: str, song: str) -> str | None:
    query = quote(f"{artist} {song}")
    url = f"https://music.apple.com/us/search?term={query}"

    #Open itunes and look for the song:
    driver = get_global_driver()
    driver.get(url)
    time.sleep(2)

    soup = BeautifulSoup(driver.page_source, "html.parser")

    #Open the link of the song:
    song_link = None
    for a in soup.select(".track-lockup__title a[data-testid='click-action']"):
        if song.lower() in a.get_text(strip=True).lower():
            song_link = a["href"]
            break

    if not song_link:
        print("Song not found")
    else:
        slog(f"Found song link: {song_link}")
        driver.get(song_link)
        time.sleep(2)

    song_soup = BeautifulSoup(driver.page_source, "html.parser")
    album_name = extract_from_itunes_soup(song_soup, artist, song)
    if is_blacklisted_album(album_name):
        return None
    else:
        return album_name or None