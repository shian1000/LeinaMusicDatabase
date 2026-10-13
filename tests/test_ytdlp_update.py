"""Daily yt-dlp update check (utils/youtube/ytdlp_update.py): comparing the
installed version with PyPI's (2026.08.19 == 2026.8.19, PyPI unreachable =
no offer), and re-pinning requirements.txt after an update. PyPI and pip are
never called."""

import sys
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.youtube import ytdlp_update as u


@pytest.mark.parametrize("installed, latest, expected", [
    ("2026.7.4", "2026.08.19", ("2026.7.4", "2026.08.19")),
    ("2026.8.19", "2026.08.19", None),
    ("2026.8.19", "2026.7.4", None),
    ("2026.8.19", None, None),  # PyPI unreachable
    (None, "2026.08.19", None),  # yt-dlp not installed
])
def test_available_update(monkeypatch, installed, latest, expected):
    monkeypatch.setattr(u, "installed_version", lambda package="yt-dlp": installed)
    monkeypatch.setattr(u, "latest_version", lambda timeout=None: latest)
    assert u.available_update() == expected


def test_latest_version_swallows_network_errors(monkeypatch):
    def offline(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(u.requests, "get", offline)
    assert u.latest_version() is None


def test_repin_requirements_rewrites_only_changed_pins(tmp_path, monkeypatch):
    path = tmp_path / "requirements.txt"
    path.write_text("deno==2.9.7\n# comment\nyt-dlp==2026.7.4\nyt-dlp-ejs==0.7.0\nnot-installed==1.0\n")
    installed = {"deno": "2.9.7", "yt-dlp": "2026.8.19", "yt-dlp-ejs": "0.8.0"}
    monkeypatch.setattr(u, "installed_version", lambda name: installed.get(name))

    changed = u.repin_requirements(path)

    assert changed == ["yt-dlp 2026.7.4 -> 2026.8.19", "yt-dlp-ejs 0.7.0 -> 0.8.0"]
    assert path.read_text() == "deno==2.9.7\n# comment\nyt-dlp==2026.8.19\nyt-dlp-ejs==0.8.0\nnot-installed==1.0\n"


def test_failed_install_leaves_requirements_alone(monkeypatch, tmp_path):
    def pip_fails(*a, **k):
        raise u.subprocess.CalledProcessError(1, "pip", stderr="boom")
    monkeypatch.setattr(u.subprocess, "run", pip_fails)
    monkeypatch.setattr(u, "repin_requirements", lambda: pytest.fail("re-pinned after a failed install"))

    with pytest.raises(u.subprocess.CalledProcessError):
        u.install_ytdlp("2026.8.19")
