import importlib.util
import os
import sys
from pathlib import Path

import requests

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))


def load_wikipedia_fetcher_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "utils"
        / "discoveries"
        / "discovery_modules"
        / "2wikipedia_fetcher.py"
    )
    spec = importlib.util.spec_from_file_location("wikipedia_fetcher", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_get_album_name_returns_none_when_wikipedia_page_json_decode_fails(monkeypatch):
    module = load_wikipedia_fetcher_module()

    monkeypatch.setattr(module, "_search_with_retry", lambda query: ["Some page"])

    def raise_json_error(*args, **kwargs):
        raise requests.exceptions.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(module.wikipedia, "page", raise_json_error)

    assert module.get_album_name("Lykke Li", "Sadness Is a Blessing") is None
