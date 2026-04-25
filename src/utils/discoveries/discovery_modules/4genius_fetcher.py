from utils.selenium_sessions import get_global_driver

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import re
from utils.debug import slog
from utils.text_utils import remove_brackets

def get_album_name(artist: str, title: str) -> str | None:
    driver = get_global_driver()
    
    # Search via Genius search page
    query = f"{artist} {title}".replace(" ", "%20")
    driver.get(f"https://genius.com/search?q={query}")
    slog(query)
    time.sleep(2)  # let the page breathe

    #TODO So when I get to this page and click the first result, I need to look for the song name. Click it. And only then look for album
    
    try:
        # Find first song result and click it
        link = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='-lyrics']"))
        )
        song_url = link.get_attribute("href")
        driver.get(song_url)
        time.sleep(2)
        
        # Find album link
        album_tag = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/albums/']"))
        )

        album_found = remove_brackets(album_tag.text.strip())
        return album_found
    
    except Exception as e:
        print(f"Failed: {e}")
        return None