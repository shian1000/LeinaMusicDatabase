"""Who a name or a title credits, as text only - no database: a joined
artist name ("Sw@da x Maxim", "A feat. B, C") read as several artists
(propose_artist_split(); artist_splitting.py turns a proposal into artist
rows, always after the owner's review) and a title's featured-artist credits
("Song ft. X", "Song (feat. X)") - read (feat_credits()), moved out
(split_feat_credits(), the import's and requests' rule, sanitize's
"Featured artists" step) or left out for comparing (strip_feat_credits())."""
import re
from dataclasses import dataclass, field

from config.constants import ARTIST_JOINING_WORDS
from utils.common.normalizer import normalize
from utils.common.text_utils import is_soundtrack_artist
from utils.common.title_patterns import BRACKETED, FEAT_OR_WITH_WORD, FEAT_WORD

# One "(...)" / "[...]" group with the whitespace before it
BRACKET_RE = re.compile(r"\s*" + BRACKETED)
# A bracket's content that starts a credit: "(feat. X)", "(with X)"
FEAT_WORD_RE = re.compile(r"^\s*" + FEAT_OR_WITH_WORD + r"\s", re.IGNORECASE)


# ==================== A joined artist name ====================

# "A feat. B", "A ft. B", "A featuring B", "A (feat. B)" - everything after is a guest
_FEAT = re.compile(r"\s*\(?\s*\b" + FEAT_WORD + r"\s+", re.IGNORECASE)
# Between equal artists. " x " is lowercase-only on purpose: an uppercase X is
# usually part of a name ("Final Fantasy X", "Malcolm X"). "and"/"i" are left
# out - too many band names ("Hall and Oates" style) use them.
_MAIN_SEPARATOR = re.compile(r",\s+|\s+&\s+|\s+x\s+|\s+/\s+|\s+\+\s+|\s+vs\.?\s+")
# Strong enough to pre-select a proposal on its own; "&" and "," aren't
# ("Simon & Garfunkel", "Earth, Wind & Fire").
_CONFIDENT_SEPARATOR = re.compile(r"\s+x\s+")
# The uppercase " X " left out above - accepted only when every part already
# exists as an artist ("Coldplay X BTS" yes, "Final Fantasy X OST" no).
UPPERCASE_X_SEPARATOR = re.compile(r"\s+X\s+")
# The "and"/"i" left out above - accepted only when some part already exists
# as an artist (see ARTIST_JOINING_WORDS), and never pre-selected unless all do.
JOINING_WORD_SEPARATOR = re.compile(
    r"\s+(?:" + "|".join(map(re.escape, ARTIST_JOINING_WORDS)) + r")\s+", re.IGNORECASE
)
# "Group (A, B, C)" - the bracket lists the lineup ("Męskie Granie Orkiestra
# 2026 (Igor Herbut, Zalia, Vito Bambino)", "Inpatient (Ren x Chris Webby)").
_LINEUP_BRACKET = re.compile(r"^(?P<group>[^()]+?)\s*\((?P<lineup>[^()]+)\)\s*$")


@dataclass
class SplitProposal:
    original: str
    main: list[str]
    feat: list[str] = field(default_factory=list)
    confident: bool = False

    @property
    def names(self) -> list[str]:
        return [*self.main, *self.feat]


def _split_slashes(part: str) -> list[str]:
    """Spaceless "/" splits only when it can't be one name: "Ben Folds/Nick
    Hornby" or "KLEKS/IGO/Mrozu" yes, "AC/DC" no."""
    pieces = [p.strip() for p in part.split("/")]
    if len(pieces) > 2 or any(" " in p for p in pieces):
        return pieces
    return [part]


def _split_names(text: str) -> list[str]:
    names = []
    for part in _MAIN_SEPARATOR.split(text):
        names.extend(_split_slashes(part.strip()))
    return [n.strip(" )") for n in names if n.strip(" )")]


def _has_unbalanced_brackets(text: str) -> bool:
    return text.count("(") != text.count(")")


def _propose_lineup_split(name: str) -> SplitProposal | None:
    """"Group (A, B)" -> Group + A + B, all main. Only for a bracket of two
    or more names - "Artist (PL)" is one artist - and never pre-selected:
    whether the lineup is co-performers or guests is the user's call."""
    match = _LINEUP_BRACKET.match(name)
    if not match or _FEAT.search(match.group("lineup")):
        return None
    lineup = _split_names(match.group("lineup"))
    if len(lineup) < 2:
        return None
    names = []
    for n in [match.group("group").strip(), *lineup]:
        if normalize(n) and normalize(n) not in {normalize(m) for m in names}:
            names.append(n)
    return SplitProposal(name, names, [], False) if len(names) >= 2 else None


def propose_artist_split(name: str) -> SplitProposal | None:
    """Parse a joined artist name, or None when it doesn't look like several
    artists. Purely textual - the caller decides with the user."""
    if not name or is_soundtrack_artist(name):
        return None

    lineup_proposal = _propose_lineup_split(name)
    if lineup_proposal:
        return lineup_proposal

    feat_match = _FEAT.search(name)
    main_text, feat_text = (name[:feat_match.start()], name[feat_match.end():]) if feat_match else (name, "")
    main, feat = _split_names(main_text), _split_names(feat_text)

    seen, unique_main, unique_feat = set(), [], []
    for names, out in ((main, unique_main), (feat, unique_feat)):
        for n in names:
            key = normalize(n)
            if key and key not in seen:
                seen.add(key)
                out.append(n)
    if not unique_main or len(unique_main) + len(unique_feat) < 2:
        return None
    # A split through a bracket ("X (A" + "B)") would create broken names
    if any(_has_unbalanced_brackets(n) for n in unique_main + unique_feat):
        return None

    confident = bool(feat_match or _CONFIDENT_SEPARATOR.search(main_text))
    # "Big Brother & the Holding Company" - a "the ..." part is a band name;
    # "Pezet - Małolat feat. Małpa" - a dash means the split is incomplete
    if any(n.lower().startswith("the ") for n in unique_main[1:]) or any(" - " in n for n in unique_main + unique_feat):
        confident = False
    return SplitProposal(name, unique_main, unique_feat, confident)


# ==================== A title's featured artists ====================

_FEAT_CREDIT_RE = re.compile(r"(?:^|\s)" + FEAT_WORD + r"\s+(.+)$", re.IGNORECASE)


def feat_credits(title: str) -> list[str]:
    """The featured artists a title credits, bracketed or not: "Hollywood
    feat. Snoop Dogg & Jamie Principle (Visualiser)" -> ["Snoop Dogg",
    "Jamie Principle"]."""
    credits = []
    for match in BRACKET_RE.finditer(title or ""):
        if FEAT_WORD_RE.match(match.group(1).strip() + " "):
            credits.append(match.group(1).strip())
    bare = _FEAT_CREDIT_RE.search(BRACKET_RE.sub("", title or ""))
    if bare:
        credits.append(bare.group(0).strip())
    names = []
    for credit in credits:
        split = propose_artist_split(f"X {credit}")
        names += split.feat if split else []
    return names


# An unbracketed credit up to the next bracket: "(Remix)" after it stays
_UNBRACKETED_FEAT_RE = re.compile(r"(?:^|\s)" + FEAT_WORD + r"\s+[^()\[\]]+", re.IGNORECASE)


def split_feat_credits(title: str) -> tuple[str, list[str]]:
    """(title without its featured-artist credits, the artists they name):
    "Billionaire ft. Bruno Mars" -> ("Billionaire", ["Bruno Mars"]). A title
    that is nothing but a credit, or names nobody, comes back unchanged."""
    names = feat_credits(title)
    if not names:
        return title, []
    bare = BRACKET_RE.sub(lambda m: "" if FEAT_WORD_RE.match(m.group(1).strip() + " ") else m.group(0), title)
    bare = " ".join(_UNBRACKETED_FEAT_RE.sub(" ", bare).split())
    return (bare, names) if bare else (title, [])


def strip_feat_credits(title: str) -> str:
    """`title` without its featured-artist credits, bracketed or not -
    whatever names they hold ("Song (feat. X) (Remix)" -> "Song (Remix)");
    the title itself when that would leave nothing. For comparing; to move
    the credits out, split_feat_credits() names them too."""
    bare = BRACKET_RE.sub(lambda m: "" if FEAT_WORD_RE.match(m.group(1).strip() + " ") else m.group(0), title or "")
    bare = " ".join(_UNBRACKETED_FEAT_RE.sub(" ", bare).split())
    return bare or title
