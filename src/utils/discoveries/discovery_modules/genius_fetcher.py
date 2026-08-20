from utils.common.selenium_sessions import get_global_driver

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import re
from utils.common.debug import slog
from utils.common.text_utils import remove_brackets, is_blacklisted_album, normalize, similarity
from utils.discoveries.discovery_result import DiscoveryResult
from difflib import SequenceMatcher

MODULE_NAME = "Genius lyrics fetcher"

def _url_slug(url: str) -> str:
    """"metronomy-love-letters-lyrics" -> "metronomy love letters lyrics" """
    return url.rstrip('/').split('/')[-1].replace('-', ' ').replace(' lyrics', '')

def _title_portion_of_slug(slug: str, artist: str) -> str:
    """Best-effort strip of the leading artist name from a "artist title" slug,
    so a title-only similarity check upstream isn't diluted by the artist words."""
    norm_slug = normalize(slug)
    norm_artist = normalize(artist)
    if norm_artist and norm_slug.startswith(norm_artist):
        return norm_slug[len(norm_artist):].strip()
    return norm_slug

def title_matches_url(title: str, url: str, threshold: float = 0.6) -> bool:
    slog("title_matches_url function", priority=1)
    """Check if a song title roughly matches a Genius lyrics URL."""
    slug = _url_slug(url)
    slog(slug, priority=1)
    title_slug = normalize(title)
    url_slug = normalize(slug)

    ratio = similarity(title_slug, url_slug)
    slog(ratio, priority=1)
    return ratio >= threshold

def get_album_name(artist: str, title: str) -> str | None:
    slog("Genius", priority=1)
    driver = get_global_driver()
    
    # Search via Genius search page
    query = f"{artist} {title}".replace(" ", "%20")
    url = (f"https://genius.com/search?q={query}")
    slog(url, priority=1)
    driver.get(url)
    slog(query, priority=1)
    time.sleep(2)  # let the page breathe
    
    try:
        # Find first song result and click it
        links = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href*='-lyrics']"))
        )
        slog(links, priority=1)

        song_url = None
        for link in links:
            slog(link, priority=1)
            href = link.get_attribute("href")
            slog(href, priority=1)
            if href and title_matches_url(title, href):
                song_url = href
                slog("match", priority=1)
                break

        if not song_url:
            slog("No matching Genius result found")
            return None

        slog(song_url)
        driver.get(song_url)
        time.sleep(2)
        
        # Find album link
        album_tag = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/albums/']"))
        )

        album_found = remove_brackets(album_tag.text.strip())
        if is_blacklisted_album(album_found):
            return None
        else:
            matched_title = _title_portion_of_slug(_url_slug(song_url), artist)
            return DiscoveryResult(album=album_found, matched_title=matched_title)
    
    except Exception as e:
        slog(f"Failed: {e}")
        return None