"""Artist and title in colour, so they stand out in long lists of files and
songs: artist cyan, title yellow, details (album, year, a score) dimmed.

questionary prompts don't interpret ANSI codes (they show up as garbage), so
a label is built as prompt_toolkit (style, text) tokens - usable directly as a
questionary.Choice title - and turned into ANSI only for print()."""

ARTIST_STYLE = "fg:ansibrightcyan"
TITLE_STYLE = "fg:ansibrightyellow"
DETAIL_STYLE = "fg:ansibrightblack"

_ANSI = {ARTIST_STYLE: "\033[96m", TITLE_STYLE: "\033[93m", DETAIL_STYLE: "\033[90m"}
_RESET = "\033[0m"


def song_tokens(artist: str | None, title: str | None, details: str | None = None) -> list:
    """"Artist - Title  (details)" as (style, text) tokens; "?" for a
    missing artist or title."""
    tokens = [(ARTIST_STYLE, artist or "?"), ("", " - "), (TITLE_STYLE, title or "?")]
    if details:
        tokens += [("", "  "), (DETAIL_STYLE, f"({details})")]
    return tokens


def as_ansi(tokens: list) -> str:
    """The tokens as a string with ANSI colours, for print()."""
    return "".join(f"{_ANSI[style]}{text}{_RESET}" if style in _ANSI else text for style, text in tokens)


def colored_song(artist: str | None, title: str | None, details: str | None = None) -> str:
    return as_ansi(song_tokens(artist, title, details))
