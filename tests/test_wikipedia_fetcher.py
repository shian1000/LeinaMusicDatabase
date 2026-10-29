import importlib.util
import os
import sys
from pathlib import Path

import requests

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))


def load_wikipedia_fetcher_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "utils"
        / "discoveries"
        / "discovery_modules"
        / "wikipedia_fetcher.py"
    )
    spec = importlib.util.spec_from_file_location("wikipedia_fetcher", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_get_album_name_returns_none_when_wikipedia_page_json_decode_fails(monkeypatch):
    module = load_wikipedia_fetcher_module()

    monkeypatch.setattr(module, "_search_with_retry", lambda query: ["Some page"])

    def raise_json_error(*args, **kwargs):
        raise requests.exceptions.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(module.wikipedia, "page", raise_json_error)

    assert module.get_album_name("Lykke Li", "Sadness Is a Blessing") is None


def test_wikipedia_requests_carry_the_projects_user_agent(monkeypatch):
    """DSC-09: page fetches sent a placeholder contact (your-email@example.com)."""
    from config.constants import MUSICBRAINZ_API_USER_AGENT
    module = load_wikipedia_fetcher_module()
    sent = {}

    def fake_get(url, headers=None, timeout=None):
        sent.update(headers or {})
        raise requests.exceptions.ConnectionError("offline")

    monkeypatch.setattr(module.requests, "get", fake_get)
    assert module._fetch_soup("https://en.wikipedia.org/wiki/X") is None
    assert sent["User-Agent"] == MUSICBRAINZ_API_USER_AGENT
    assert module.wikipedia.wikipedia.USER_AGENT == MUSICBRAINZ_API_USER_AGENT


def test_get_album_name_returns_a_discovery_result(monkeypatch):
    # DSC-11: the same result shape as the other modules, not a bare str
    from types import SimpleNamespace
    from utils.discoveries.discovery_result import DiscoveryResult

    module = load_wikipedia_fetcher_module()
    page = SimpleNamespace(title="Sadness Is a Blessing", url="https://en.wikipedia.org/wiki/x")
    monkeypatch.setattr(module, "_fetch_wikipedia_page", lambda artist, title: page)
    monkeypatch.setattr(module, "_fetch_soup", lambda url: object())
    monkeypatch.setattr(module, "_extract_from_infobox", lambda soup, title: "So Sad So Sexy (Lykke Li album)")

    result = module.get_album_name("Lykke Li", "Sadness Is a Blessing")

    assert result == DiscoveryResult(album="So Sad So Sexy")


# A franchise page found for "Initial D - Night" (the title cut at "&"): its
# "See also" list held "Wangan Midnight", and "See also" came back as the album
FRANCHISE_PAGE = """
<html><body>
<div class="mw-heading mw-heading2"><h2>Initial D Fourth Stage D Selection</h2></div>
<ul><li>"Night of Fire" - Niko</li><li>"Running in the 90s" - Max Coveri</li></ul>
<div class="mw-heading mw-heading2"><h2>See also</h2></div>
<ul><li>Wangan Midnight, another car racing-themed manga</li><li>Night Racer</li></ul>
</body></html>
"""


def _franchise_soup():
    from bs4 import BeautifulSoup
    return BeautifulSoup(FRANCHISE_PAGE, "html.parser")


def test_track_listing_skips_the_see_also_section():
    module = load_wikipedia_fetcher_module()

    album = module._extract_from_track_listing(_franchise_soup(), "Initial D", "Night Racer")

    assert album is None


def test_track_listing_matches_the_title_as_whole_words():
    module = load_wikipedia_fetcher_module()
    soup = _franchise_soup()

    # A part of a word ("Fir" in "Fire", "Night" in "Midnight") no longer matches
    assert module._extract_from_track_listing(soup, "Initial D", "Night of Fire") == \
        "Initial D Fourth Stage D Selection"
    assert module._extract_from_track_listing(soup, "Initial D", "Fir") is None
    assert module._contains_words("Wangan Midnight, another manga", "Night") is False
    assert module._contains_words('"Night & Day" - Initial D', "night & day") is True
