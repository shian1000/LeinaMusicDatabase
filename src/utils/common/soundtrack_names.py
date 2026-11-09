"""What a name says about a soundtrack, as text only: the film / game / series
it belongs to and the one album name stored for it, "<work> OST". No database -
utils/database/soundtrack_albums.py compares these with the catalog."""

import re

from config.constants import SOUNDTRACK_ALBUM_SUFFIX, SOUNDTRACK_WEAK_ARTIST_ENDINGS
from utils.common.normalizer import normalize

# Only at the end of a name: in "Noir OST I" or "Soundtrack Vol.1 (Ep1+2)" what
# follows tells apart several albums of one work
_ENDING = re.compile(
    r"[\s\-–—:,]*[(\[]?\s*(?<!\w)(?:(?:the|official|original)\s+)*"
    r"(?:(?:motion\s+picture|video\s+game|game|anime|film|movie|tv|series)\s+)?"
    r"(?:soundtracks?|o\.s\.t\.?|ost)\s*[)\]]?\s*$",
    re.IGNORECASE,
)


def _named(work: str) -> str | None:
    work = work.strip()
    return work if any(ch.isalnum() for ch in work) else None


def soundtrack_work(name: str | None) -> str | None:
    """The work a name ending in a soundtrack word is about ("Tekken 5
    Soundtrack" -> "Tekken 5"), None for any other name."""
    found = _ENDING.search(name or "")
    return _named(name[:found.start()]) if found else None


def weak_soundtrack_work(name: str | None) -> str | None:
    """The work a name may be about, judging by an ending other names have too
    ("Chrono Cross Music", but also "Amiga Music"): only ever offered."""
    for ending in SOUNDTRACK_WEAK_ARTIST_ENDINGS:
        found = re.match(rf"(.+?)[\s\-–—:,]+{re.escape(ending)}\s*$", name or "", re.IGNORECASE)
        if found:
            return _named(found.group(1))
    return None


def soundtrack_album(work: str) -> str:
    return f"{work} {SOUNDTRACK_ALBUM_SUFFIX}"


def work_key(work: str) -> str:
    """What two spellings of a work share ("The Rick and Morty" / "Rick and
    Morty", "DuckTales" / "Duck Tales")."""
    key = normalize(work) or work.lower()
    return key.removeprefix("the ").replace(" ", "")
