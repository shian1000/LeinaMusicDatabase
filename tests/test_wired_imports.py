"""The owner's rule (ADR-0006): MusicDatabaseWired imports only the standard library,
the shared core and the approved packages, never MusicDatabaseHomeServer. Adding a
package means editing this list, wired/DEPENDENCIES.md and wired/requirements.txt."""

import ast
import re
import sys
from pathlib import Path

import pytest

WIRED = Path(__file__).resolve().parents[1] / "wired"
DIRECT_IMPORTS = {"fastapi", "pydantic", "questionary", "starlette", "uvicorn"}
ALLOWED = set(sys.stdlib_module_names) | DIRECT_IMPORTS | {"musicdatabase_core", "musicdatabase_wired"}
SOURCES = [WIRED / "mdw.py", *sorted((WIRED / "musicdatabase_wired").rglob("*.py"))]


def _pinned() -> dict[str, str]:
    lines = (WIRED / "requirements.txt").read_text().splitlines()
    return dict(line.split("==") for line in lines if line and not line.startswith("#"))


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_wired_imports_only_what_is_approved(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module]
        else:
            continue
        for name in names:
            assert name.split(".")[0] in ALLOWED, f"{path.name} imports {name}"


def test_every_package_is_pinned_and_documented():
    documented = dict(re.findall(r"^\| `([^`]+)` \| ([^ |]+) \|", (WIRED / "DEPENDENCIES.md").read_text(), re.M))
    assert documented == _pinned()


def test_nothing_that_could_send_telemetry_is_approved():
    """FastAPI requires opentelemetry-api, which does nothing without an SDK or an exporter."""
    assert not [name for name in _pinned() if "opentelemetry" in name and name != "opentelemetry-api"]


def test_telemetry_is_switched_off_in_the_app():
    source = (WIRED / "musicdatabase_wired/app.py").read_text()
    assert "telemetry=NO_TELEMETRY" in source
    settings = ast.literal_eval(re.search(r"^NO_TELEMETRY = (\{.*\})$", source, re.M).group(1))
    assert settings == {"tracing": False, "metrics": False, "logs": False, "operation_spans": False,
                        "auto_configure": False}


def test_the_venv_holds_exactly_the_approved_packages():
    site_packages = list((WIRED / "venv/lib").glob("python*/site-packages"))
    if not site_packages:
        pytest.skip("wired/venv isn't installed here")
    def key(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name).lower()

    installed = {}
    for info in site_packages[0].glob("*.dist-info"):
        name, version = info.name.removesuffix(".dist-info").rsplit("-", 1)
        if key(name) != "pip":
            installed[key(name)] = version
    assert installed == {key(name): version for name, version in _pinned().items()}
