"""Main menu -> Help: every menu label its texts quote ("...") is a real one,
so the help can't drift from the menus unnoticed."""
import re
from pathlib import Path

from menu.main_menu import help as help_menu

SRC = Path(__file__).resolve().parents[1] / "src"


def _sources() -> str:
    help_file = Path(help_menu.__file__).resolve()
    return "\n".join(p.read_text() for folder in ("menu", "utils/ui") for p in (SRC / folder).rglob("*.py")
                     if p.resolve() != help_file)


def test_quoted_labels_are_real_menu_entries():
    sources = _sources()
    # A label may be broken over two lines of the text
    quoted = {" ".join(label.split()) for text in help_menu.TOPICS.values()
              for label in re.findall(r'"([^"]+)"', text)}
    assert quoted
    missing = [label for label in sorted(quoted) if f'"{label}"' not in sources and f'f"{label}{{' not in sources]
    assert missing == []


def test_the_main_menu_has_help():
    source = (SRC / "menu" / "main_menu" / "__init__.py").read_text()
    assert '"Help": help_menu' in source


def test_texts_fit_100_columns():
    too_long = [line for text in help_menu.TOPICS.values() for line in text.split("\n") if len(line) > 100]
    assert too_long == []
