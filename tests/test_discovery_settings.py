"""Which discovery modules run and in what order (utils/discoveries/
discovery_settings.py): the saved config reconciled against the modules that
exist - a removed one dropped, a new one appended and on, the user's order
and switches kept, the file written only when something changed (DSC-20).
Temp config files only."""
import json

import pytest

from utils.discoveries import discovery_settings as ds

DEFAULT = ["music_brainz_fetcher", "itunes_fetcher", "wikipedia_fetcher"]


@pytest.fixture
def config(tmp_path):
    return tmp_path / "discovery_modules_config.json"


def _write(path, order, enabled):
    path.write_text(json.dumps({"order": order, "enabled": enabled}))


def test_without_a_file_every_default_module_is_on_in_the_default_order(config):
    result = ds._reconcile_config(DEFAULT, str(config), DEFAULT)
    assert result == {"order": DEFAULT, "enabled": {m: True for m in DEFAULT}}
    assert not config.exists()  # the defaults as they are: nothing to save


def test_the_users_order_and_switches_are_kept(config):
    _write(config, ["wikipedia_fetcher", "music_brainz_fetcher", "itunes_fetcher"],
           {"wikipedia_fetcher": True, "music_brainz_fetcher": False, "itunes_fetcher": True})

    result = ds._reconcile_config(DEFAULT, str(config), DEFAULT)
    assert result["order"] == ["wikipedia_fetcher", "music_brainz_fetcher", "itunes_fetcher"]
    assert result["enabled"]["music_brainz_fetcher"] is False


def test_a_removed_module_is_dropped_and_a_new_one_appended_switched_on(config):
    _write(config, ["genius_fetcher", "itunes_fetcher", "music_brainz_fetcher"],
           {"genius_fetcher": True, "itunes_fetcher": False, "music_brainz_fetcher": True})

    result = ds._reconcile_config(["music_brainz_fetcher", "itunes_fetcher", "spotify_fetcher"],
                                  str(config), DEFAULT)
    assert result["order"] == ["itunes_fetcher", "music_brainz_fetcher", "spotify_fetcher"]
    assert result["enabled"] == {"itunes_fetcher": False, "music_brainz_fetcher": True, "spotify_fetcher": True}
    assert json.loads(config.read_text()) == result


def test_the_file_is_written_only_when_something_changed(config, monkeypatch):
    _write(config, DEFAULT, {m: True for m in DEFAULT})
    saved = []
    monkeypatch.setattr(ds, "_save_config", lambda cfg, path: saved.append(cfg))

    ds._reconcile_config(DEFAULT, str(config), DEFAULT)
    assert saved == []
    ds._reconcile_config(DEFAULT[:2], str(config), DEFAULT)
    assert len(saved) == 1


def test_a_module_missing_from_enabled_counts_as_on(config):
    _write(config, DEFAULT, {"itunes_fetcher": False})
    result = ds._reconcile_config(DEFAULT, str(config), DEFAULT)
    assert result["enabled"] == {"music_brainz_fetcher": True, "itunes_fetcher": False, "wikipedia_fetcher": True}


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '"x"'])
def test_an_unreadable_or_odd_file_falls_back_to_the_defaults(config, text):
    config.write_text(text)
    result = ds._reconcile_config(DEFAULT, str(config), DEFAULT)
    assert result == {"order": DEFAULT, "enabled": {m: True for m in DEFAULT}}


def test_album_and_year_configs_are_separate_files(tmp_path, monkeypatch):
    albums, years = tmp_path / "albums.json", tmp_path / "years.json"
    monkeypatch.setattr(ds, "DISCOVERY_MODULES_CONFIG_FILE", str(albums))
    monkeypatch.setattr(ds, "DISCOVERY_MODULES_YEAR_CONFIG_FILE", str(years))

    ds.reconcile_discovery_config(["itunes_fetcher", "genius_fetcher"])
    ds.reconcile_year_discovery_config(["spotify_fetcher"])
    assert json.loads(albums.read_text())["order"] == ["itunes_fetcher", "genius_fetcher"]
    assert ds.load_year_discovery_config()["order"] == ["spotify_fetcher"]
