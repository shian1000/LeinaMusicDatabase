import wikipedia
import requests
import re
from bs4 import BeautifulSoup
from utils.debug import slog

def clean_album_title(album_name, artist):
    # Try artist-specific pattern first
    pattern = re.compile(r"\s*\(\s*" + re.escape(artist) + r"\s+album\s*\)", re.IGNORECASE)
    cleaned = pattern.sub("", album_name).strip()
    if cleaned != album_name:
        return cleaned

    # Fallback: strip any " (... album)" suffix
    fallback = re.compile(r"\s*\([^)]*\balbum\b[^)]*\)", re.IGNORECASE)
    return fallback.sub("", album_name).strip()

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

    headers = {"User-Agent": "SongAlbumFinder/1.0 (your-email@example.com)"}
    response = requests.get(page.url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")
    slog(response)
    # slog(soup)




    # --- Case 1: page title matches the song — look for "from the album" in infobox ---
    if title.lower() in page.title.lower():
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






    # --- Case 2: page title doesn't match — search track listing tables for the song ---
    else:
        GENERIC_HEADINGS = {
            "track listing", "tracklist", "track list",
            "disc 1", "disc 2", "disc 3",
            "side a", "side b", "side c", "side d",
            "standard edition", "deluxe edition", "bonus tracks",
        }

        track_listing_album = None

        # Find all heading elements - either bare h1-h4 or h1-h4 wrapped in a div.mw-heading
        heading_containers = []
        for node in soup.find_all(re.compile(r"^h[1-4]$")):
            parent = node.parent
            if parent and "mw-heading" in " ".join(parent.get("class", [])):
                # Use the wrapper div as the anchor for sibling traversal
                heading_containers.append((node.get_text(strip=True), parent))
            else:
                # Bare heading, use it directly
                heading_containers.append((node.get_text(strip=True), node))

        for heading_text, anchor in heading_containers:
            slog(f"Case 2 - checking heading: {heading_text}")

            for sibling in anchor.find_next_siblings():
                if sibling.name and re.match(r"^h[1-4]$", sibling.name):
                    break
                # Also stop if we hit another mw-heading wrapper div
                if sibling.name == "div" and "mw-heading" in " ".join(sibling.get("class", [])):
                    break

                found_container = None

                # Case A: fancy Wikipedia tracklist table
                if sibling.name == "table" and "tracklist" in sibling.get("class", []):
                    found_container = sibling
                    slog("Case 2 - Case A: tracklist table found under heading")
                    slog(heading_text)
                else:
                    nested = sibling.find("table", class_="tracklist")
                    if nested:
                        found_container = nested
                        slog("Case 2 - Case A (nested): tracklist table found nested under heading")
                        slog(heading_text)

                # Case B: simple <ol> or <ul> list
                if found_container is None and sibling.name in ("ol", "ul"):
                    found_container = sibling
                    slog("Case 2 - Case B: plain ol/ul list found under heading")
                    slog(heading_text)

                # Case C: list nested inside a div
                if found_container is None:
                    nested_list = sibling.find(["ol", "ul"])
                    if nested_list:
                        found_container = nested_list
                        slog("Case 2 - Case C: nested list found inside sibling under heading")
                        slog(heading_text)

                if found_container is not None:
                    container_text = found_container.get_text(separator=" ", strip=True)
                    if title.lower() in container_text.lower():
                        slog("Case 2 - song title found in container")
                        if heading_text.lower() in GENERIC_HEADINGS:
                            track_listing_album = page.title
                            slog("Case 2 - heading is generic, using page.title as album")
                            slog(track_listing_album)
                        else:
                            track_listing_album = heading_text
                            slog("Case 2 - heading is specific, using heading as album")
                            slog(track_listing_album)
                        break
                    else:
                        slog("Case 2 - container found but song title not in it")
                        slog(container_text[:200])

            if track_listing_album is not None:
                break

        if track_listing_album is None:
            slog("Case 2 - no track listing container matched, returning None")


        if(track_listing_album):
            track_listing_album = clean_album_title(track_listing_album, artist)
            return track_listing_album
        else:
            return None
