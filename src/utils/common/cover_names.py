"""Whether a title says the song is a cover and of whom, as text only - no
database, utils/database/covers.py owns that. "Creep (Radiohead cover)", "Gaz
(cover Bunker 84)", "Chop Suey (System of a Down) Jazz Cover", and "Look At Me
Now (Cover by Karmin)", where the name is the performer and the stored artist
the one covered."""
import re
from collections.abc import Callable
from dataclasses import dataclass

from utils.common.normalizer import normalize
from utils.common.title_patterns import YOUTUBE_BRACKETED

_COVER = r"(?<!\w)(?:cover|кавер)(?!\w)"
_COVER_RE = re.compile(_COVER, re.IGNORECASE)
# "Twinz (Deep Cover '98)" is that song's name
_NOT_A_COVER_RE = re.compile(r"(?<!\w)(?:deep|under|take)\s+cover(?!\w)|(?<!\w)cover\s+(?:me|up)(?!\w)", re.IGNORECASE)
_BRACKET_RE = re.compile(YOUTUBE_BRACKETED)
_COVERED_BY_RE = re.compile(r"^\s*cover(?:ed)?\s+by\s+(.+?)\s*$", re.IGNORECASE)
_COVER_FIRST_RE = re.compile(rf"^\s*{_COVER}\s+(?:of\s+)?(.+?)\s*$", re.IGNORECASE)
_NAME_THEN_COVER_RE = re.compile(rf"^(.*?)\s*{_COVER}(.*)$", re.IGNORECASE | re.DOTALL)
_TRAILING_COVER_RE = re.compile(rf"^(.*?)[\s\-–—|:]*{_COVER}[\s.]*$", re.IGNORECASE | re.DOTALL)
_NAME_SEPARATOR_RE = re.compile(r"\s+[-–—]\s+")
# Words saying how it's played, not whose it is
_STYLE_WORDS = frozenset({
    "acoustic", "akustycznie", "bardcore", "bluegrass", "epic", "guitar", "instrumental", "jazz", "live", "metal",
    "orchestral", "piano", "rock", "song", "synthwave", "ukrainian", "violin", "vintage", "vocal", "80s", "90s",
    "medieval",
})
_EDGES = " -–—|:;,./~•·"


@dataclass(frozen=True)
class CoverReading:
    """`title`: without the credit, when the credit was understood; else the
    title unchanged. `covered`: the name given as the original artist, None
    when the title only says it's a cover. `performer`: the name given as who
    plays it, when that isn't the entry's artist ("Cover by Karmin")."""
    title: str
    covered: str | None = None
    performer: str | None = None
    credit_read: bool = False


def _without_style(text: str) -> str:
    words = text.split()
    while words and words[-1].strip(_EDGES).lower() in _STYLE_WORDS:
        words.pop()
    while words and words[0].strip(_EDGES).lower() in _STYLE_WORDS:
        words.pop(0)
    return " ".join(words).strip(_EDGES)


def _is_style_only(text: str) -> bool:
    return all(word.strip(_EDGES).lower() in _STYLE_WORDS for word in text.split() if word.strip(_EDGES))


def _same_person(name: str | None, artist: str | None) -> bool:
    """"Anna" of "annapantsu", "Gingertail" of "Alina Gingertail"."""
    first, second = normalize(name or ""), normalize(artist or "")
    return bool(first and second) and (first in second or second in first)


def _cut(title: str, start: int, end: int) -> str:
    return re.sub(r"\s{2,}", " ", f"{title[:start]} {title[end:]}").strip(_EDGES)


def _name_of_bracket(inside: str) -> tuple[str | None, bool]:
    """The covered artist a bracket holding "cover" names, and whether the
    bracket was understood at all (False: something else follows the word)."""
    if (first := _COVER_FIRST_RE.match(inside)) and not _NAME_THEN_COVER_RE.match(inside)[1].strip(_EDGES):
        return (_without_style(first[1]) or None), True
    match = _NAME_THEN_COVER_RE.match(inside)
    if not _is_style_only(match[2]):
        return None, False
    # "Bonnie Tyler - Holding out for a hero cover song": the name comes first
    name = _without_style(_NAME_SEPARATOR_RE.split(match[1])[0])
    return (name or None), True


def _read_bracket(title: str, bracket: re.Match, artist: str | None) -> CoverReading | None:
    inside = bracket[1]
    if by := _COVERED_BY_RE.match(inside):
        rest = _cut(title, *bracket.span())
        named = list(_BRACKET_RE.finditer(rest))
        # "Gravity (Sara Bareilles) 【covered by Anna】"
        if named and named[-1].end() == len(rest):
            return CoverReading(_cut(rest, *named[-1].span()), named[-1][1].strip(),
                                None if _same_person(by[1], artist) else by[1], True)
        if _same_person(by[1], artist):
            return CoverReading(rest, None, None, True)
        return CoverReading(rest, artist, by[1], True)
    if not _COVER_RE.search(inside) or _NOT_A_COVER_RE.search(inside):
        return None
    name, understood = _name_of_bracket(inside)
    if not understood:
        return CoverReading(title)
    rest = _cut(title, *bracket.span())
    if not rest:
        return CoverReading(title)
    # "Dirge for the Planet (Gingertail Cover)" by Alina Gingertail names who plays it
    if name and _same_person(name, artist):
        return CoverReading(rest, None, None, True)
    return CoverReading(rest, name, None, True)


def read_cover(artist: str | None, title: str | None,
               known_artist: Callable[[str], str | None] | None = None) -> CoverReading | None:
    """What `title` says about being a cover, None when it says nothing.
    `known_artist` (a name -> the artist's stored name, or None) lets a credit
    with no bracket be told from the title: "Crazy in Love Beyonce Cover"."""
    if not title or not _COVER_RE.search(title.replace("covered by", "cover").replace("Covered by", "cover")):
        return None
    if _NOT_A_COVER_RE.search(title):
        return None
    for bracket in _BRACKET_RE.finditer(title):
        if reading := _read_bracket(title, bracket, artist):
            return reading
    trailing = _TRAILING_COVER_RE.match(title)
    if not trailing:
        return CoverReading(title)
    before = trailing[1].rstrip(_EDGES)
    words = before.split()
    while words and words[-1].lower() in _STYLE_WORDS:
        words.pop()
    bare = " ".join(words)
    # "Chop Suey (System of a Down) Jazz Cover"
    named = list(_BRACKET_RE.finditer(bare))
    if named and named[-1].end() == len(bare):
        rest = _cut(bare, *named[-1].span())
        return CoverReading(rest, named[-1][1].strip(), None, True) if rest else CoverReading(title)
    if known_artist:
        for start in range(1, len(words)):
            if name := known_artist(" ".join(words[start:])):
                if _same_person(name, artist):
                    break
                return CoverReading(" ".join(words[:start]).strip(_EDGES), name, None, True)
    # "Plastic Love Cover": nothing but the word; anything else may be a name
    if bare and bare == before or len(words) != len(before.split()):
        return CoverReading(bare.strip(_EDGES) if known_artist else title, None, None, bool(known_artist))
    return CoverReading(title)


def covered_artist_name(song) -> str | None:
    """The name of the artist `song` is a cover of, or None."""
    return getattr(getattr(song, "covered_artist", None), "name", None)


def cover_credit(name: str) -> str:
    return f"({name} cover)"


def title_with_cover(song) -> str:
    """"Crazy in Love (Beyoncé cover)": how a cover's title is shown and
    written to a file, which read_cover() reads back."""
    name = covered_artist_name(song)
    return f"{song.title} {cover_credit(name)}" if name else song.title
