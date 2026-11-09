"""Which project a name or title says it's from (Tiny Desk, Like a Version), as
text only, given the dictionary - no database, utils/database/projects.py owns
that. Only whole spellings from the dictionary count: "Session" alone says
nothing, so a word that general never belongs there."""
import re
from dataclasses import dataclass
from functools import lru_cache

from utils.common.cover_names import title_with_cover
from utils.common.normalizer import composed

# What may be left at either end of a text once the project is cut out
_EDGE_SEPARATORS = " -–—|:;,./~•·"
_BRACKET_PAIRS = {"(": ")", "[": "]", "{": "}", "【": "】", "（": "）", "「": "」", "［": "］"}
_LEAD_IN = r"(?:(?:live\s+)?(?:at|on|from|for)\s+|@\s*)?"
_TRAILING_WORD = r"(?:\s+(?:sessions?|concert|performance))?"
_REPEATED_SEPARATOR_RE = re.compile(r"(\s[-–—|:]\s+)(?:[-–—|:]\s+)+")
_EMPTY_LIST_ITEM_RE = re.compile(r"\s*,\s*(?=[)\]}】）」］])|(?<=[(\[{【（「［])\s*,\s*")
_ARTIST_TITLE_RE = re.compile(r"\s+[-–—]\s+|\s+:\s+")
_SEPARATORS_AROUND_A_NAME = ":|-–—/"
# A spelling naming a performance itself: only then can a title be the whole
# set of whoever it names. "Męskie Granie" - "Rilke" is just a song
_WHOLE_SET_WORD_RE = re.compile(r"(?<!\w)(?:concerts?|koncert|sessions?|sesja)(?!\w)", re.IGNORECASE)


@dataclass(frozen=True)
class ProjectName:
    name: str
    aliases: tuple = ()
    channels: tuple = ()

    @property
    def spellings(self) -> tuple:
        return (self.name, *self.aliases)


@dataclass
class ProjectReading:
    """What a song's names say once the project is out of them. `whole`: not
    one song but the artist's whole set ("yeule: Tiny Desk Concert").
    `guessed`: the artist was read from the title, only offered, never taken
    without a yes. `artist_unknown`: the names don't say who the artist is."""
    project: str
    artist: str | None
    title: str | None
    whole: bool = False
    guessed: bool = False
    artist_unknown: bool = False


def name_key(name: str | None) -> str:
    """Case, punctuation and spaces don't tell two spellings apart."""
    return " ".join(re.findall(r"\w+", composed(name or "").casefold()))


def split_names(text: str | None) -> tuple:
    """The comma-separated spellings of an aliases / channels column."""
    return tuple(part.strip() for part in (text or "").split(",") if part.strip())


def song_project_name(song) -> str | None:
    """The name of the project `song` belongs to, or None."""
    return getattr(getattr(song, "project", None), "name", None)


def is_project_of(song, project: str | None) -> bool:
    """`project` (None: no project) is the one `song` belongs to."""
    return name_key(song_project_name(song)) == name_key(project)


def title_with_project(song) -> str:
    """"Boys (Like a Version)" for a file: read back into the project and the
    title by cut_project(). A title already naming it stays."""
    project = song_project_name(song)
    title = title_with_cover(song)
    if not project or name_key(project) in name_key(song.title):
        return title
    return f"{title} ({project})"


def search_title(song) -> str:
    """The title to look for on YouTube: "Boys Like a Version", or it finds
    the studio song."""
    project = song_project_name(song)
    if not project or name_key(project) in name_key(song.title):
        return song.title
    return f"{song.title} {project}"


@lru_cache(maxsize=256)
def _spelling_re(spelling: str) -> re.Pattern | None:
    words = re.findall(r"\w+", composed(spelling))
    if not words:
        return None
    core = r"[\W_]*".join(map(re.escape, words))
    return re.compile(rf"(?<!\w){_LEAD_IN}(?P<core>{core}){_TRAILING_WORD}(?!\w)", re.IGNORECASE)


def _find(text: str | None, projects: list) -> tuple | None:
    """(project, match) of the longest spelling in `text`, or None."""
    if not text:
        return None
    text = composed(text)
    best = None
    for project in projects:
        for spelling in project.spellings:
            pattern = _spelling_re(spelling)
            found = pattern.search(text) if pattern else None
            if found and (best is None or len(found.group("core")) > len(best[1].group("core"))):
                best = (project, found)
    return best


def _named_by(name: str | None, projects: list, channels: bool = True) -> ProjectName | None:
    """The project `name` is a spelling (or, with `channels`, a channel) of."""
    key = name_key(name)
    if not key:
        return None
    return next((p for p in projects if key in map(name_key, (*p.spellings, *(p.channels if channels else ())))), None)


def _enclosing_bracket(text: str, start: int, end: int) -> tuple | None:
    """(where it opens, where it closes) of the bracket around text[start:end]."""
    for position in range(start - 1, -1, -1):
        closing = _BRACKET_PAIRS.get(text[position])
        if closing:
            close = text.find(closing, end)
            return (position, close) if close >= 0 else None
        if text[position] in _BRACKET_PAIRS.values():
            return None
    return None


def cut_project(text: str | None, projects: list) -> tuple | None:
    """(project, `text` without it, the spelling as written), or None when
    `text` names none: "Boys (Like a Version)" -> "Boys", "Daughter | Tiny Desk
    Concert" -> "Daughter". A bracket left with nothing but numbers in it goes
    too: "(Męskie Granie 2018)" is that edition."""
    found = _find(text, projects)
    if found is None:
        return None
    project, match = found
    text = composed(text)
    start, end = match.span()
    bracket = _enclosing_bracket(text, start, end)
    if bracket and not re.sub(r"[\W\d_]", "", text[bracket[0] + 1:start] + text[end:bracket[1]]):
        start, end = bracket[0], bracket[1] + 1
    rest = text[:start] + text[end:]
    rest = _EMPTY_LIST_ITEM_RE.sub("", " ".join(rest.split()))
    rest = _REPEATED_SEPARATOR_RE.sub(r"\1", rest).strip(_EDGE_SEPARATORS)
    return project, rest, match.group("core")


def _whole_set_title(project: ProjectName, spelling: str) -> str:
    """A whole set's title: the spelling found, without the channel publishing
    it - "NPR Music Tiny Desk Concert" -> "Tiny Desk Concert"."""
    for channel in project.channels:
        pattern = _spelling_re(channel)
        found = pattern.match(spelling) if pattern else None
        if found and spelling[found.end():].strip(_EDGE_SEPARATORS):
            return spelling[found.end():].strip(_EDGE_SEPARATORS)
    return spelling


def _set_apart(text: str, match: re.Match) -> bool:
    """Does a separator or a bracket divide the project from the rest of
    `text`? "Kaunas Acoustics : Liūdni slibinai" - but "Męskie Granie Orkiestra
    2022" is one name, the project only part of it."""
    before, after = text[:match.start()].rstrip(), text[match.end():].lstrip()
    return bool(before and before[-1] in _SEPARATORS_AROUND_A_NAME + "([{【（「［"
                or after and after[0] in _SEPARATORS_AROUND_A_NAME + ")]}】）」］")


def read_project(artist: str | None, title: str | None, projects: list, *, album: str | None = None,
                 channel: str | None = None, raw_text: str | None = None, guess: bool = False) -> ProjectReading | None:
    """The project a song's artist, title, album, channel or whole read text
    names, with the artist and title it leaves, or None. `guess`: the title
    of an artist that is only the project ("NPR Music Tiny Desk Concert" -
    "Daughter") read as the artist, only when the spelling names a concert or
    session (for a list the owner ticks; an import leaves it, there is no one
    to ask). An artist whose name only contains the project keeps it."""
    if not projects:
        return None
    in_artist = cut_project(artist, projects)
    found_in_artist = _find(artist, projects)
    if in_artist and in_artist[1] and not _set_apart(composed(artist), found_in_artist[1]):
        in_artist = None
    if in_artist and in_artist[1]:
        # "Kaunas Acoustics : Liūdni slibinai"
        in_title = cut_project(title, projects)
        return ProjectReading(in_artist[0].name, in_artist[1], in_title[1] if in_title and in_title[1] else title)
    in_title = cut_project(title, projects)
    if in_title:
        project, rest, spelling = in_title
        whole_set = bool(_WHOLE_SET_WORD_RE.search(spelling))
        if _named_by(artist, [project], channels=False) and not whole_set:
            return ProjectReading(project.name, artist, rest or spelling, artist_unknown=True)
        if _named_by(artist, [project]):
            # A channel as the artist: who plays is in the title
            if not rest:
                return ProjectReading(project.name, artist, _whole_set_title(project, spelling), whole=True,
                                      guessed=True, artist_unknown=True)
            parts = _ARTIST_TITLE_RE.split(rest, maxsplit=1)
            if len(parts) == 2 and all(parts):
                return ProjectReading(project.name, parts[0], parts[1])
            return ProjectReading(project.name, rest, _whole_set_title(project, spelling), whole=True)
        return ProjectReading(project.name, artist, rest or _whole_set_title(project, spelling),
                              whole=not rest and whole_set)
    if in_artist:
        project, _, spelling = in_artist
        if guess and title and _WHOLE_SET_WORD_RE.search(spelling):
            return ProjectReading(project.name, title, _whole_set_title(project, spelling), whole=True, guessed=True)
        return ProjectReading(project.name, artist, title, artist_unknown=True)
    found = _find(album, projects) or _find(raw_text, projects) or found_in_artist
    project = found[0] if found else _named_by(channel, projects, channels=False)
    return ProjectReading(project.name, artist, title) if project else None
