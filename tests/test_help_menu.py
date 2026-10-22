"""Main menu -> Help: every menu label its texts quote ("...") is a real one,
so the help can't drift from the menus unnoticed; the start-up topic shows
the switches as they are now; every library the program imports itself is
described, with the same text in README.md -> Libraries."""
import re
from pathlib import Path

from menu.main_menu import help as help_menu

SRC = Path(__file__).resolve().parents[1] / "src"


def _texts() -> list[str]:
    return [help_menu.topic_text(title) for title in help_menu.TOPICS]


def _sources() -> str:
    help_file = Path(help_menu.__file__).resolve()
    return "\n".join(p.read_text() for folder in ("menu", "utils/ui") for p in (SRC / folder).rglob("*.py")
                     if p.resolve() != help_file)


def test_quoted_labels_are_real_menu_entries():
    sources = _sources()
    # A label may be broken over two lines of the text
    quoted = {" ".join(label.split()) for text in _texts()
              for label in re.findall(r'"([^"]+)"', text)}
    assert quoted
    missing = [label for label in sorted(quoted) if f'"{label}"' not in sources and f'f"{label}{{' not in sources]
    assert missing == []


def test_the_main_menu_has_help():
    source = (SRC / "menu" / "main_menu" / "__init__.py").read_text()
    assert '"Help": help_menu' in source


def test_texts_fit_100_columns():
    too_long = [line for text in _texts() for line in text.split("\n") if len(line) > 100]
    assert too_long == []


def test_the_start_up_topic_shows_each_switch_as_it_is_now(monkeypatch, tmp_path):
    from utils.common import startup_settings as switches
    from utils.database import public_database

    monkeypatch.setattr(switches, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(public_database, "daily_publish_enabled", lambda: False)
    switches.set_enabled(switches.BACKUP, False)

    text = help_menu.topic_text("What happens at start-up")

    assert "[OFF] Daily backup" in text and "[OFF] Daily publish to GitHub" in text
    assert all(f"[ON ] {switch.label}" in text for switch in switches.SWITCHES if switch is not switches.BACKUP)
    assert "Database upgrades (migrations)" in text.split("Optional")[0]


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_every_library_the_program_imports_is_described():
    import ast
    from importlib import metadata

    described = {_norm(part.strip()) for name, _ in help_menu.LIBRARIES for part in name.split(",")}
    distributions = metadata.packages_distributions()
    imported = set()
    for path in list(SRC.rglob("*.py")) + [SRC.parent / "main.py"]:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
    missing = []
    for module in sorted(imported):
        owners = {_norm(d) for d in distributions.get(module, [])}
        # A namespace like google.* is shared by several distributions: one described is enough
        if owners and not owners & described:
            missing.append((module, sorted(owners)))
    assert missing == []


def test_readme_describes_the_libraries_like_help():
    readme = " ".join((SRC.parent / "README.md").read_text().split())
    for name, text in help_menu.LIBRARIES:
        assert f"**{name}** — {' '.join(text.split())}" in readme
    assert help_menu.LIBRARIES_NOTE in readme
