"""Whether a title says the song is a version of another - live, a remix, an
acoustic take - as text only; utils/database/versions.py owns the database.
"Boję się (Live)", "Recover (Cid Rim Remix)", "Matylda (na żywo, akustycznie)",
"Engel (extended version)". A marker that only says which release the recording
is on ("Radio Edit", "Album Version", "Remastered") isn't a version: the same
recording."""
import re
from dataclasses import dataclass

from config.constants import (
    VERSION_ACOUSTIC,
    VERSION_DEMO,
    VERSION_EDIT,
    VERSION_EXTENDED,
    VERSION_INSTRUMENTAL,
    VERSION_KIND_LABELS,
    VERSION_LIVE,
    VERSION_OTHER,
    VERSION_REMIX,
)
from utils.common.normalizer import normalize
from utils.common.title_patterns import YOUTUBE_BRACKETED

_BRACKET_RE = re.compile(r"\s*" + YOUTUBE_BRACKETED)
# A spaced dash; what follows the last ones may be a marker (" - Live at Wembley")
_SPACED_DASH_RE = re.compile(r"\s+[-–—]\s+")
_NO_BRACKETS_RE = re.compile(r"^[^()\[\]【】（）［］]+$")

# First match wins: "na żywo, akustycznie" is live, "Extended Orchestral Version" extended
_KIND_WORDS = (
    # A contest's or a radio's performance: "Eurovision Song Contest 2011", "BBC Session"
    # ("Eurovision Version" is its studio edit: a version, not live)
    (VERSION_LIVE, r"live|na zywo|koncert\w*|concert|eurovision song contest|eurovision \d{4}|sessions?"),
    (VERSION_ACOUSTIC, r"acoustic|akustycz\w*|unplugged"),
    (VERSION_INSTRUMENTAL, r"instrumental|instrumentalna"),
    (VERSION_DEMO, r"demo"),
    (VERSION_REMIX, r"remix|rmx|rework|bootleg|mashup|mix|dub"),
    (VERSION_EXTENDED, r"extended|long version|long mix"),
    (VERSION_EDIT, r"edit"),
    (VERSION_OTHER, r"version|wersja"),
)
_KIND_RES = [(kind, re.compile(rf"(?:^| )(?:{words})(?: |$)")) for kind, words in _KIND_WORDS]
# Which release, not which recording
_SAME_RECORDING_RE = re.compile(
    r"^(?:\d{4} )?(?:radio|album|single|original|clean|explicit|explicit album|mono|stereo|lp|ep)"
    r"(?: (?:edit|version|mix|cut))?$|remaster")
# Brackets other readings own: a cover credit, a featured artist
_OTHER_CREDIT_RE = re.compile(r"(?:^| )(?:cover|covered|feat|ft|featuring|with)(?: |$)")


@dataclass(frozen=True)
class VersionReading:
    """`title` without the marker; `kind` one of constants.VERSION_KINDS;
    `label` the marker as written, None when it only names the kind ("live")."""
    title: str
    kind: str
    label: str | None = None


def version_kind_of(marker: str) -> str | None:
    """The kind of version a marker ("Live Session", "Cid Rim Remix") names,
    None when it names none or only a release ("Radio Edit")."""
    key = normalize(marker)
    if not key or _SAME_RECORDING_RE.search(key) or _OTHER_CREDIT_RE.search(key):
        return None
    return next((kind for kind, words in _KIND_RES if words.search(key)), None)


def _reading(title: str, start: int, end: int, marker: str) -> VersionReading | None:
    kind = version_kind_of(marker)
    rest = re.sub(r"\s{2,}", " ", f"{title[:start]} {title[end:]}").strip(" -–—.,")
    if kind is None or not rest:
        return None
    label = marker.strip()
    return VersionReading(rest, kind, None if normalize(label) == normalize(VERSION_KIND_LABELS[kind]) else label)


def read_version(title: str | None) -> VersionReading | None:
    """What `title` says about being a version: its last bracket naming one
    ("Song (Live) (Remastered)" is live), else a spaced-dash suffix ("Song -
    Acoustic Version"), the shortest naming one: "I Love Belarus - Eurovision
    Song Contest 2011 - BBC One" is live, "Eurovision Song Contest 2011 - BBC
    One" its label; None when it says nothing."""
    if not title:
        return None
    for bracket in reversed(list(_BRACKET_RE.finditer(title))):
        if reading := _reading(title, bracket.start(), bracket.end(), bracket[1]):
            return reading
    for dash in reversed(list(_SPACED_DASH_RE.finditer(title))):
        marker = title[dash.end():].strip()
        if not _NO_BRACKETS_RE.match(marker):
            break
        if reading := _reading(title, dash.start(), len(title), marker):
            return reading
        key = normalize(marker)
        if _SAME_RECORDING_RE.search(key) or _OTHER_CREDIT_RE.search(key):
            break  # "... - Radio Version" names the release: nothing further left is a marker
    return None


def version_marker(song) -> str | None:
    """"(Live)", "(Cid Rim Remix)": how a version is marked after its title,
    None for a song that isn't one."""
    kind = getattr(song, "version_kind", None)
    if not kind:
        return None
    return f"({getattr(song, 'version_label', None) or VERSION_KIND_LABELS.get(kind, kind)})"


def title_with_version(song) -> str:
    """"Boję się (Live)": a version's title as shown, written to a file and
    compared when matching; read_version() reads it back. Any other song's
    title as it is."""
    marker = version_marker(song)
    return f"{song.title} {marker}" if marker else song.title
