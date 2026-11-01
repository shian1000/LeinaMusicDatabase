"""One song as an import, a request or a file reads it - the artist, title
and the rest that becomes a Song, plus where it came from and how it was
read, so nothing the first read knew is lost on the way.

For now a dict of the keys every caller already uses ("artist_name",
"title", ... - the import_engine metadata dict), so it goes wherever such a
dict went: read and written with ["key"] / .get(), copied with {**entry}
(the copy is a plain dict without the attributes), equal to a dict of the
same keys. The same keys are attributes too (entry.artist, entry.title),
and what a dict can't hold - where it came from - only attributes. Callers
move from keys to attributes step by step; then it stops being a dict.
Never a database row: a Song given an existing artist joins the session.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from utils.common.title_patterns import YEAR

# attribute -> key in the metadata dict
METADATA_KEYS = {
    "artist": "artist_name",
    "title": "title",
    "album": "album",
    "year": "year",
    "language": "language",
    "origin": "origin",
    "youtube_video_id": "youtube_video_id",
    "duration_s": "duration_s",          # tells a full-album recording from a track
    "feat_artists": "feat_artists",      # credited as featured when the song is created
    "label": "_label",                   # how the entry is named in the import's messages
    "unsplit_title": "unsplit_title",    # a file name split on a bare "-", read whole as the title
}


@dataclass(frozen=True)
class Removed:
    """A piece a cleanup step cut out: the step (sanitize.STEPS), the field
    ("title", "artist", "album"; None = the whole text before it was split)
    and exactly the text cut ("(Official Video)", "- 2002")."""
    step: str
    field: Optional[str]
    text: str


@dataclass
class Hints:
    """What the cut pieces and the title say about the song - only offered,
    never stored without the owner's yes (a bracketed year can be a live
    recording's, a "(from ...)" is not always the album)."""
    year: Optional[int] = None            # "(1991)", " - 2002", "[театр PipinO, 2013]"
    remaster: Optional[int] = None        # "(2015 Remastered)" - the remaster's year, not the song's
    featured: list = field(default_factory=list)  # "ft. X" in the title, or moved out of it
    track_number: Optional[int] = None    # "T03: " / "03. " the title starts with
    from_work: Optional[str] = None       # "(from “Chess”)", "(Baby Driver Soundtrack)"
    official_video: bool = False          # "(Official Video)", "Music Video" was cut

    def __bool__(self) -> bool:
        return any((self.year, self.remaster, self.featured, self.track_number, self.from_work, self.official_video))


_YEAR_RE = re.compile(rf"\b({YEAR})\b")
_REMASTER_RE = re.compile(r"remaster", re.IGNORECASE)
_QUOTES = "\"'“”„‘’『』「」"
_FROM_RE = re.compile(rf"^[(\[]?\s*from\s+[{_QUOTES}]?(.+?)[{_QUOTES}]?\s*[)\]]?$", re.IGNORECASE)
_SOUNDTRACK_RE = re.compile(r"^[(\[]?\s*(.+?)\s+(?:remaster(?:ed)?\s+)?(?:original\s+)?"
                            r"(?:motion\s+picture\s+|video\s+game\s+|game\s+)?"
                            r"(?:soundtrack|ost)\s*[)\]]?$", re.IGNORECASE)
_OFFICIAL_VIDEO_RE = re.compile(r"\bofficial\s+(?:music\s+)?video\b|\bmusic\s*video\b|\bvideo\s*clip\b|"
                                r"\bclip\s+officiel\b|\bvideoclip\b", re.IGNORECASE)
# "T03: ", "03. ", "Track 5) " - as text_utils.strip_track_number_prefix() reads it
_TRACK_NUMBER_RE = re.compile(r"^(?:(?:track|t)\s*)?(\d{1,3})\s*[.:)\-–]\s+\S", re.IGNORECASE)


def hints_from(entry: "SongEntry") -> Hints:
    """The Hints in what `entry`'s cleanup cut and in its title."""
    hints = Hints()
    for piece in entry.removed:
        if piece.field not in (None, "title"):
            continue
        text = piece.text
        year = _YEAR_RE.search(text)
        if year and _REMASTER_RE.search(text):
            hints.remaster = hints.remaster or int(year.group(1))
        elif year:
            hints.year = hints.year or int(year.group(1))
        if hints.from_work is None and (found := _FROM_RE.match(text) or _SOUNDTRACK_RE.match(text)):
            hints.from_work = found.group(1).strip(_QUOTES + " ") or None
        if _OFFICIAL_VIDEO_RE.search(text) and not re.search(r"lyric", text, re.IGNORECASE):
            hints.official_video = True
    title = entry.originals.get("title") or entry.title or ""
    if found := _TRACK_NUMBER_RE.match(title.strip()):
        hints.track_number = int(found.group(1)) or None
    if entry.feat_artists:
        hints.featured = list(entry.feat_artists)
    elif entry.title:
        from utils.common.artist_credits import split_feat_credits  # the import's own rule
        hints.featured = split_feat_credits(entry.title)[1]
    return hints


def describe_cuts(entry: "SongEntry") -> str:
    """" cut: '(Official Video)', '- 2002'; hints: year 2002, official video"
    for a trace line, or "" when nothing was cut and nothing hinted."""
    parts = []
    if entry.removed:
        parts.append("cut: " + ", ".join(repr(piece.text) for piece in entry.removed))
    hints = entry.hints
    found = [f"year {hints.year}" if hints.year else "", f"remastered {hints.remaster}" if hints.remaster else "",
             f"feat {', '.join(hints.featured)}" if hints.featured else "",
             f"track {hints.track_number}" if hints.track_number else "",
             f"from {hints.from_work!r}" if hints.from_work else "", "official video" if hints.official_video else ""]
    if found := [f for f in found if f]:
        parts.append("hints: " + ", ".join(found))
    return "; " + "; ".join(parts) if parts else ""


def _key_property(key: str) -> property:
    return property(lambda self: self.get(key), lambda self, value: self.__setitem__(key, value),
                    doc=f'entry["{key}"]')


class SongEntry(dict):
    """A song as it was read (see the module docstring). Besides the
    metadata keys:

    source     - where it came from: sanitize.SOURCE_* ("mp3", "youtube", ...)
    raw_text   - the whole text it was read from, before any split or
                 cleanup: a video's title, a file's name (without extension)
    originals  - each field as it came, before the cleanup: {"artist": ...,
                 "title": ..., "album": ...}; only the fields that came
    parsed_by  - how artist and title were told apart ("Topic channel",
                 "split Artist - Title", ...), None when they came apart
    channel    - the YouTube channel
    file       - the file it was read from
    removed    - what the cleanup cut (Removed), in order
    hints      - what that and the title say about the song (Hints)
    """

    def __init__(self, metadata: Optional[dict] = None, *, source: Optional[str] = None,
                 raw_text: Optional[str] = None, originals: Optional[dict] = None,
                 parsed_by: Optional[str] = None, channel: Optional[str] = None,
                 file: Optional[Path] = None, removed: Optional[list] = None):
        super().__init__(metadata or {})
        self.source = source
        self.raw_text = raw_text
        self.originals = dict(originals or {})
        self.parsed_by = parsed_by
        self.channel = channel
        self.file = file
        self.removed = list(removed or [])

    @property
    def hints(self) -> Hints:
        return hints_from(self)

    def copy_with(self, **changes) -> "SongEntry":
        """A copy with `changes` (attribute names: title=..., unsplit_title=None)
        and everything else, the attributes too - {**entry} loses those."""
        copy = SongEntry(self, source=self.source, raw_text=self.raw_text, originals=self.originals,
                         parsed_by=self.parsed_by, channel=self.channel, file=self.file, removed=self.removed)
        for attribute, value in changes.items():
            if attribute not in METADATA_KEYS:
                raise AttributeError(f"SongEntry has no field {attribute!r}")
            setattr(copy, attribute, value)
        return copy

    def __repr__(self) -> str:
        return f"SongEntry({dict.__repr__(self)}, source={self.source!r}, parsed_by={self.parsed_by!r})"


def as_entry(metadata) -> SongEntry:
    """`metadata` itself when it's a SongEntry, else a SongEntry of its keys
    (a copy: writing to it doesn't reach the dict)."""
    return metadata if isinstance(metadata, SongEntry) else SongEntry(metadata)


for _attribute, _key in METADATA_KEYS.items():
    setattr(SongEntry, _attribute, _key_property(_key))
