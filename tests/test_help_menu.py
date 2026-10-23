"""Main menu -> Help: every menu label its texts quote ("...") is a real one,
so the help can't drift from the menus unnoticed; the start-up topic shows
the switches as they are now; every library the program imports itself is
described, with the same text in README.md -> Libraries; "Background
services (Linux)" shows the systemd units, installed or as the setup would
write them, without touching anything (temp SYSTEMD_USER_DIR)."""
import re
from pathlib import Path

import pytest

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
    from utils.common import env_file
    from utils.common import startup_settings as switches
    from utils.database import public_database

    monkeypatch.setattr(env_file, "ENV_FILE", tmp_path / ".env")
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


# ==================== Background services (Linux) ====================

def test_the_background_services_topic_has_the_unit_files_option():
    assert "Background services (Linux)" in help_menu.TOPICS
    text = help_menu.topic_text("Background services (Linux)")
    for unit in ("musicdatabase-share.service", "musicdatabase-share.timer",
                 "musicdatabase-inbox.service", "musicdatabase-inbox.timer"):
        assert unit in text
    assert "Show the full unit files" in help_menu.TOPIC_ACTIONS["Background services (Linux)"]


@pytest.fixture
def units_dir(monkeypatch, tmp_path):
    from utils.database import sharing

    folder = tmp_path / "systemd-user"
    monkeypatch.setattr(sharing, "SYSTEMD_USER_DIR", folder)
    monkeypatch.setattr(sharing, "SHARING_DIR", tmp_path / "shared")
    monkeypatch.setattr(sharing, "install_sharing_service", lambda: pytest.fail("installed something"))
    monkeypatch.setattr(sharing, "_systemctl", lambda *a: pytest.fail("ran systemctl"))
    return folder


def test_not_installed_shows_what_the_setup_would_write_and_writes_nothing(units_dir, capsys):
    from utils.database import sharing

    help_menu.show_unit_files()

    out = capsys.readouterr().out
    assert out.count("NOT installed - what the setup would write now") == 4
    assert sharing._service_unit(sharing.server_python()).strip() in out
    assert sharing._inbox_timer_unit().strip() in out
    assert not units_dir.exists()


def test_installed_units_are_shown_as_they_are_on_disk(units_dir, capsys):
    from utils.database import sharing

    units_dir.mkdir()
    (units_dir / "musicdatabase-share.service").write_text(sharing._service_unit(sharing.server_python()))
    (units_dir / "musicdatabase-share.timer").write_text("[Timer]\nOnStartupSec=3min\n")
    before = {p.name: p.read_text() for p in units_dir.iterdir()}

    help_menu.show_unit_files()

    out = capsys.readouterr().out
    assert "musicdatabase-share.service\x1b[0m (installed - shown as it is on disk)" in out
    assert "musicdatabase-share.timer\x1b[0m (installed - shown as it is on disk; the setup would now write" in out
    assert "OnStartupSec=3min" in out
    assert out.count("NOT installed") == 2
    assert {p.name: p.read_text() for p in units_dir.iterdir()} == before


def test_the_topic_offers_the_option_and_back(monkeypatch, units_dir, capsys):
    import questionary

    answers = iter(["Show the full unit files", "Back"])

    class _Ask:
        def __init__(self, value):
            self.value = value

        def ask(self):
            return self.value

    monkeypatch.setattr(questionary, "select", lambda message, choices: _Ask(next(answers)))
    monkeypatch.setattr(help_menu, "clear_screen", lambda: None)
    help_menu._show("Background services (Linux)")
    assert "NOT installed" in capsys.readouterr().out


_PHONE_WORD = re.compile(r"\bphones?\b", re.IGNORECASE)


def _user_strings(path: Path) -> list[str]:
    """The string literals of a module (f-string parts too), without docstrings."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
                  and node.body and isinstance(node.body[0], ast.Expr)
                  and isinstance(node.body[0].value, ast.Constant)}
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings]


def test_help_and_the_sharing_setup_say_leina_radio_not_the_phone():
    """UI-21: user-facing text names the client "Leina Radio", never "the
    phone" (AGENTS.md); comments and docstrings may."""
    assert not [t for t in _texts() if _PHONE_WORD.search(t)]
    for module in ("utils/ui/sharing_wizard.py", "utils/common/sharing_setup.py",
                   "menu/main_menu/settings/__init__.py", "menu/main_menu/help.py"):
        found = [s for s in _user_strings(SRC / module) if _PHONE_WORD.search(s)]
        assert not found, f"{module}: {found}"


@pytest.mark.parametrize("module, function, header", [
    ("menu.main_menu.enter_database.manage_database.fetch_database_data", "fetch_database_data",
     "Fetch database data"),
    ("menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data",
     "fill_missing_data", "Fill missing data"),
    ("menu.main_menu.enter_database.manage_database.manual_management", "manual_management",
     "Manual management"),
])
def test_a_submenu_is_headed_by_the_label_that_opens_it(monkeypatch, module, function, header):
    """UI-23: all three were headed "Manage database", their parent's name."""
    import importlib
    menu = importlib.import_module(module)
    shown = []
    monkeypatch.setattr(menu, "execute_menu_item", lambda prompt, *a, **k: shown.append(prompt))
    getattr(menu, function)()
    assert shown == [header]
