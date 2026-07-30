import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from menu.main_menu.enter_database import download_yt_song_menu


class DummyPrompt:
    def __init__(self, *args, **kwargs):
        self._value = None

    def ask(self):
        return self._value


def test_download_yt_song_menu_uses_prompted_link(monkeypatch):
    captured = {}

    def fake_prompt(*args, **kwargs):
        return DummyPrompt()

    def fake_download(url, output_dir=None):
        captured["url"] = url
        captured["output_dir"] = output_dir
        return "/tmp/test-song.mp4"

    import importlib

    enter_database = importlib.import_module("menu.main_menu.enter_database")

    monkeypatch.setattr(enter_database, "download_youtube_video", fake_download)

    prompt = DummyPrompt()
    prompt._value = "https://www.youtube.com/watch?v=abc123"
    monkeypatch.setattr(enter_database.questionary, "text", lambda *args, **kwargs: prompt)

    result = download_yt_song_menu()

    assert result == "/tmp/test-song.mp4"
    assert captured["url"] == "https://www.youtube.com/watch?v=abc123"
    assert captured["output_dir"] is not None
