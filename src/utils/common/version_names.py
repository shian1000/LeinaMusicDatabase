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
    VERSION_KARAOKE,
    VERSION_KIND_LABELS,
    VERSION_LIVE,
    VERSION_OTHER,
    VERSION_REMIX,
    VERSIONS_NEEDING_OWN_RELEASE,
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
    # before instrumental: "Karaoke Instrumental" is sung along to
    (VERSION_KARAOKE, r"karaoke"),
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


# A release titled only as a live recording: "Live at Leeds", "Live in Gdańsk", "Koncert w
# Jarocinie", "Stones Live" - read_version() needs a bracket or a dash. Not any "Live ..."
# ("Live Through This", "Live and Let Die" are studio records), only one saying where.
_RELEASE_TITLE_RES = {
    VERSION_LIVE: re.compile(r"^(?:live|na zywo|koncert\w*|concert) (?:at|in|from|on|aus|au|w|we|z|ze|na|u|v)(?: |$)"
                             r"|(?:^| )(?:live|na zywo)$"),
}


def reads_as_version(title: str | None, kind: str) -> bool:
    """Does a release's or recording's title say it is a `kind` version - by
    its marker ("The Last Time (Live at the Beacon Theatre)", "Hits - Live")
    or, for a release, by what it is titled ("Live at Leeds")?"""
    if not title:
        return False
    reading = read_version(title)
    if reading is not None and reading.kind == kind:
        return True
    pattern = _RELEASE_TITLE_RES.get(kind)
    return bool(pattern and pattern.search(normalize(title)))


def version_needing_own_release(song) -> str | None:
    """The song's version kind when a lookup by its artist and title can't
    tell it from the original (constants.VERSIONS_NEEDING_OWN_RELEASE), else None."""
    kind = getattr(song, "version_kind", None)
    return kind if kind in VERSIONS_NEEDING_OWN_RELEASE else None


def lookup_fits_version(song, titles, release_kind: str | None = None) -> bool:
    """May an album or year an artist-and-title lookup found be saved on
    `song` without asking? Always for a song that isn't such a version
    (version_needing_own_release()); for one, only when the release found is
    that kind of version too: one of `titles` (the release's, the matched
    recording's) reads so, or the source says (`release_kind`, e.g.
    MusicBrainz's "Live" release group). Else the owner reviews the value -
    a live take otherwise gets the studio single's album and year."""
    kind = version_needing_own_release(song)
    return kind is None or release_kind == kind or any(reads_as_version(title, kind) for title in titles)
