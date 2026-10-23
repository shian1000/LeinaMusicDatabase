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
