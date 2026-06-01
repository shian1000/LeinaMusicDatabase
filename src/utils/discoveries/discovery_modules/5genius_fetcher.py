from utils.common.selenium_sessions import get_global_driver

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import re
from utils.common.debug import slog
from utils.common.text_utils import remove_brackets, is_blacklisted_album
from difflib import SequenceMatcher

MODULE_NAME = "Genius lyrics fetcher"

def slugify(text: str) -> str:
    """Convert text to a lowercase slug for comparison."""
    return re.sub(r'[^a-z0-9]', '', text.lower())

def title_matches_url(title: str, url: str, threshold: float = 0.6) -> bool:
    """Check if a song title roughly matches a Genius lyrics URL."""
    # Extract the slug part: "metronomy-love-letters-lyrics" → "metronomy love letters lyrics"
    slug = url.rstrip('/').split('/')[-1].replace('-', ' ').replace(' lyrics', '')
    title_slug = slugify(title)
    url_slug = slugify(slug)
    
    ratio = SequenceMatcher(None, title_slug, url_slug).ratio()
    return ratio >= threshold

def get_album_name(artist: str, title: str) -> str | None:
    driver = get_global_driver()
    
    # Search via Genius search page
    query = f"{artist} {title}".replace(" ", "%20")
    driver.get(f"https://genius.com/search?q={query}")
    slog(query)
    time.sleep(2)  # let the page breathe
    
    try:
        # Find first song result and click it
        links = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='-lyrics']"))
        )

        song_url = None
        for link in links:
            href = link.get_attribute("href")
            if href and title_matches_url(title, href):
                song_url = href
                break
        
        song_url = link.get_attribute("href")
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
            return album_found
    
    except Exception as e:
        slog(f"Failed: {e}")
        return None