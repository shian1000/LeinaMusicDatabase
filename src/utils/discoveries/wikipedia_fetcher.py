import wikipedia
import requests
import re
from bs4 import BeautifulSoup
from utils.debug import slog

def get_album_from_wikipedia(artist: str, title: str) -> str | None:
    """
    Search Wikipedia for a song and extract the album name from the infobox.

    Args:
        artist: The artist name
        title: The song title

    Returns:
        Album name if found, None otherwise
    """
    search_query = f"{artist} {title}"

    results = wikipedia.search(search_query)
    if not results:
        return None

    slog(results)

    try:
        page = wikipedia.page(results[0], auto_suggest=False)
    except (wikipedia.DisambiguationError, wikipedia.PageError):
        return None

    slog(f"{page.title}")

    if title.lower() not in page.title.lower():
        return None

    headers = {"User-Agent": "SongAlbumFinder/1.0 (your-email@example.com)"}
    response = requests.get(page.url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")
    slog(response)
    slog(soup)

    infobox = soup.find("table", class_=re.compile(r"\binfobox\b"))
    if not infobox:
        return None

    infobox_text = infobox.get_text(separator=" ", strip=True)
    slog(f"{infobox_text[:300]}")

    lower_infobox = infobox_text.lower()
    marker = "from the album"
    idx = lower_infobox.find(marker)

    if idx == -1:
        return None

    after_marker = infobox_text[idx + len(marker):].strip()
    after_marker = after_marker.lstrip(" \"''\u201c\u2018")

    match = re.match(r"([^\"\n\(\)]+?)(?:\s{2,}|[BRG][-\s])", after_marker)
    if not match:
        match = re.match(r"([^\",\.\n\(\)]+)", after_marker)

    if not match:
        return None

    album_name = match.group(1).strip(" \"''\u201d\u2019")
    return album_name if album_name else None