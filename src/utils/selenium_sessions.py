import threading
from typing import Optional
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

try:
    from webdriver_manager.chrome import ChromeDriverManager
    USE_WDM = True
except ImportError:
    USE_WDM = False


# -- Global driver state -------------------------------------------------------

_GLOBAL_DRIVER: Optional[webdriver.Chrome] = None
_GLOBAL_DRIVER_LOCK = threading.Lock()


def open_global_driver(headless: bool = True) -> webdriver.Chrome:
    """
    Open a Chrome WebDriver and store it globally.
    If a driver is already open, returns it without opening a new one.

    Args:
        headless: Run Chrome without a visible window (default True).
                  Set to False for debugging.

    Returns:
        The active WebDriver instance.
    """
    global _GLOBAL_DRIVER
    with _GLOBAL_DRIVER_LOCK:
        if _GLOBAL_DRIVER is None:
            _GLOBAL_DRIVER = _build_driver(headless=headless)
        return _GLOBAL_DRIVER


def get_global_driver() -> Optional[webdriver.Chrome]:
    """
    Return the current global WebDriver, or None if not yet opened.
    """
    return _GLOBAL_DRIVER


def close_global_driver() -> None:
    """
    Quit the global WebDriver and clear the global reference.
    Safe to call even if the driver was never opened.
    """
    global _GLOBAL_DRIVER
    with _GLOBAL_DRIVER_LOCK:
        if _GLOBAL_DRIVER is None:
            return
        _GLOBAL_DRIVER.quit()
        _GLOBAL_DRIVER = None

def _build_driver(headless: bool = True) -> webdriver.Chrome:
    """Build and return a new Chrome WebDriver instance."""
    options = Options()
    if headless:
        options.add_argument("--headless=new")  # Chrome 112+

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    if USE_WDM:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver