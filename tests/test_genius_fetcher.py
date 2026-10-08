import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from utils.discoveries.discovery_modules.genius_fetcher import title_matches_url


def test_short_title_with_long_artist_matches_its_own_url():
    # Regression: the full "artist title" slug scored 0.45 against "cruisin",
    # so the correct search result was rejected.
    assert title_matches_url(
        "Cruisin'", "https://genius.com/Childish-gambino-cruisin-lyrics", "Childish Gambino"
    )


def test_long_title_still_matches():
    assert title_matches_url(
        "Love Letters", "https://genius.com/Metronomy-love-letters-lyrics", "Metronomy"
    )


def test_different_song_by_same_artist_is_rejected():
    assert not title_matches_url(
        "Cruisin'", "https://genius.com/Childish-gambino-redbone-lyrics", "Childish Gambino"
    )


def test_same_title_by_different_artist_is_rejected():
    # The artist isn't stripped from someone else's slug, so it still dilutes the ratio.
    assert not title_matches_url(
        "Cruisin'", "https://genius.com/Smokey-robinson-cruisin-lyrics", "Childish Gambino"
    )
