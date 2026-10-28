"""The shared Chrome driver (utils/common/selenium_sessions.py): the driver
build is mocked, no browser is started."""
import pytest

from utils.common import selenium_sessions


@pytest.mark.parametrize("headless", [True, False])
def test_open_global_driver_passes_headless_on(monkeypatch, headless):
    # COM-04: open_global_driver(headless=False) built a headless driver anyway
    built = []
    monkeypatch.setattr(selenium_sessions, "_GLOBAL_DRIVER", None)
    monkeypatch.setattr(selenium_sessions, "_build_driver", lambda headless=True: built.append(headless) or "driver")

    assert selenium_sessions.open_global_driver(headless=headless) == "driver"
    assert built == [headless]


def test_a_chrome_that_wont_start_is_explained_in_english(monkeypatch):
    # COM-33: the only Polish message in an English program
    from selenium.common.exceptions import WebDriverException

    def no_chrome(options=None):
        raise WebDriverException("DevToolsActivePort file doesn't exist")
    monkeypatch.setattr(selenium_sessions.shutil, "which", lambda name: "/usr/bin/chromium")
    monkeypatch.setattr(selenium_sessions.webdriver, "Chrome", no_chrome)

    with pytest.raises(selenium_sessions.ChromeDriverLaunchError, match="Couldn't start Chrome/Chromium"):
        selenium_sessions._build_driver()
