"""Messages for the user, in one style.

The rule (AGENTS.md -> "Output"): what the user should read goes through
this module - say() for a plain line, ok() / warn() / error() for a coloured
one, show() for a line with rich markup, review_header() above a batch
review. Diagnostics go through utils.common.debug instead: flog() for
anything that has to be diagnosable afterwards (always in debug.log),
slog() / mlog() while debugging. Never a message for the user through
slog(): without a .debug file nobody sees it.

It lives in utils/common, not utils/ui, because the import and the data
layer still print progress themselves (ADR-0001 keeps them free of utils/ui).

Names and titles are escaped wherever they reach rich: a title like
"Song [Live]" would otherwise lose its "[Live]" as an unknown markup tag.
show() takes markup as it is, so the caller escapes with styled() or
escape(). A plain print() is fine for a line that never goes through rich -
questionary prompts show rich markup literally, so a line printed right
above one uses the ANSI colours below (see song_colors for artist / title)."""

from rich.console import Console
from rich.markup import escape

__all__ = ["console", "escape", "styled", "show", "say", "ok", "warn", "error", "review_header",
           "RED", "GREEN", "YELLOW", "BLUE", "CYAN", "BOLD", "DIM", "RESET", "REVIEW_WIDTH"]

# One console for the whole app. It looks up sys.stdout on every print, so
# pytest's capsys / capfd see its output. soft_wrap: no line breaks of its
# own - the terminal wraps, and a long path stays copyable
console = Console(soft_wrap=True)

# ANSI colours for a plain print()
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[90m"
RESET = "\033[0m"

REVIEW_WIDTH = 70


def styled(text: object, style: str) -> str:
    """text, escaped, as rich markup in the given style - for show()."""
    return f"[{style}]{escape(str(text))}[/{style}]"


def show(markup: str) -> None:
    """Print a line of rich markup; names in it must already be escaped."""
    console.print(markup)


def say(text: object) -> None:
    """Print a plain line, taken literally (no markup, no highlighting)."""
    console.print(str(text), markup=False, highlight=False)


def ok(text: object) -> None:
    """Print a line in green: something done."""
    console.print(styled(text, "green"), highlight=False)


def warn(text: object) -> None:
    """Print a line in yellow: something skipped or worth a look."""
    console.print(styled(text, "yellow"), highlight=False)


def error(text: object) -> None:
    """Print a line in red: something failed."""
    console.print(styled(text, "red"), highlight=False)


def review_header(title: str, *notes: str) -> None:
    """The banner above a batch review: a blank line, a rule, the title and
    any notes (one line each), a rule."""
    print("\n" + "=" * REVIEW_WIDTH)
    print(title)
    for note in notes:
        print(note)
    print("=" * REVIEW_WIDTH)
