"""Artist and title in colour, so they stand out in long lists of files and
songs: artist cyan, title yellow, details (album, year, a score) dimmed. Artists a
title credits as featured ("Ангела (feat. Крихітка)") get the artist colour,
the "(feat. " and ")" around them stay plain.

questionary prompts don't interpret ANSI codes (they show up as garbage), so
a label is built as prompt_toolkit (style, text) tokens - usable directly as a
questionary.Choice title - and turned into ANSI only for print()."""

import re

ARTIST_STYLE = "fg:ansibrightcyan"
TITLE_STYLE = "fg:ansibrightyellow"
DETAIL_STYLE = "fg:ansibrightblack"

_ANSI = {ARTIST_STYLE: "\033[96m", TITLE_STYLE: "\033[93m", DETAIL_STYLE: "\033[90m"}
_RESET = "\033[0m"
_RICH = {ARTIST_STYLE: "bright_cyan", TITLE_STYLE: "bright_yellow", DETAIL_STYLE: "bright_black"}

# Where a title's featured-artist credit starts: a bracketed "(feat. " or a
# bare " ft. " (the names themselves come from song_duplicates.feat_credits())
_FEAT_START_RE = re.compile(r"[(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s|(?:^|\s)(?:feat\.?|ft\.?|featuring)\s",
                            re.IGNORECASE)


def title_tokens(title: str) -> list:
    """The title as (style, text) tokens: title colour, except a
    featured-artist credit - its names in the artist colour, the "(feat. ",
    " & " and ")" around them plain."""
    from utils.database.song_duplicates import feat_credits

    names = feat_credits(title)
    tokens, position = [], 0
    for credit in _FEAT_START_RE.finditer(title) if names else ():
        if credit.start() < position:
            continue
        tokens += [(TITLE_STYLE, title[position:credit.start()]), ("", title[credit.start():credit.end()])]
        position = credit.end()
        bracketed = title[credit.start()] in "(["
        closing = title.find(")" if title[credit.start()] == "(" else "]", position) if bracketed else -1
        while names:
            found = title.find(names[0], position)
            # The next name is past this credit's bracket - it belongs to a later one
            if found < 0 or (closing >= 0 and found > closing):
                break
            tokens += [("", title[position:found]), (ARTIST_STYLE, names[0])]
            position = found + len(names.pop(0))
        if closing >= position:
            tokens.append(("", title[position:closing + 1]))
            position = closing + 1
    tokens.append((TITLE_STYLE, title[position:]))
    return [(style, text) for style, text in tokens if text]


def song_tokens(artist: str | None, title: str | None, details: str | None = None) -> list:
    """"Artist - Title  (details)" as (style, text) tokens; "?" for a
    missing artist or title."""
    tokens = [(ARTIST_STYLE, artist or "?"), ("", " - "), *(title_tokens(title) if title else [(TITLE_STYLE, "?")])]
    if details:
        tokens += [("", "  "), (DETAIL_STYLE, f"({details})")]
    return tokens


def as_ansi(tokens: list) -> str:
    """The tokens as a string with ANSI colours, for print()."""
    return "".join(f"{_ANSI[style]}{text}{_RESET}" if style in _ANSI else text for style, text in tokens)


def as_rich_markup(tokens: list) -> str:
    """The tokens as rich markup, for rich's print()."""
    from rich.markup import escape

    return "".join(f"[{_RICH[style]}]{escape(text)}[/{_RICH[style]}]" if style in _RICH else escape(text)
                   for style, text in tokens)


def colored_song(artist: str | None, title: str | None, details: str | None = None) -> str:
    return as_ansi(song_tokens(artist, title, details))
