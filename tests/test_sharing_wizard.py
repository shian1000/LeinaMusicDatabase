"""The sharing setup wizard: its logic (utils/common/sharing_setup.py - each
setting's validation and suggestion, the checks' parsing) and its flow
(utils/ui/sharing_wizard.py - saving only what's picked, stopping at any
step, running again, the restart after a change). Every .env is a temp file;
prompts, systemd, loginctl and the network are faked. Nothing in the
checks is done without a yes, and a no is listed as not done."""
import os
import urllib.error

import pytest

from utils.common import sharing_setup as setup
from utils.common.env_file import read_env_value, save_env_value
from utils.ui import sharing_wizard as wiz


@pytest.fixture
def env(tmp_path):
    path = tmp_path / ".env"
    path.write_text("SMB_USERNAME=someone\n")
    return path


@pytest.fixture
def fake_mounts(monkeypatch):
    """Paths in the returned set count as mount points."""
    mounts = set()
    real = os.path.ismount
    monkeypatch.setattr(setup.os.path, "ismount", lambda p: str(p) in mounts or real(p))
    return mounts


# ==================== validation ====================

def test_an_empty_value_never_warns(env):
    assert all(setup.value_warning(step.name, "", env) is None for step in setup.SETTING_STEPS)


def test_lan_interface_must_exist(env):
    assert "no network interface" in setup.value_warning("SHARING_LAN_INTERFACE", "nosuchif0", env)
    assert setup.value_warning("SHARING_LAN_INTERFACE", "lo", env) is None


def test_paths_must_be_full(env):
    assert "full path" in setup.value_warning("SHARING_DIR", "Shared", env)


def test_drive_mount_must_be_mounted(env, tmp_path, fake_mounts):
    assert "isn't a mount point" in setup.value_warning("SHARING_DRIVE_MOUNT", str(tmp_path), env)
    fake_mounts.add(str(tmp_path))
    assert setup.value_warning("SHARING_DRIVE_MOUNT", str(tmp_path), env) is None


def test_sharing_dir_on_the_system_disk_may_be_missing_or_existing(env, tmp_path):
    assert setup.value_warning("SHARING_DIR", str(tmp_path / "new"), env) is None
    assert setup.value_warning("SHARING_DIR", str(tmp_path), env) is None


def test_sharing_dir_must_be_on_the_drive(env, tmp_path, fake_mounts):
    drive = tmp_path / "drive"
    drive.mkdir()
    fake_mounts.add(str(drive))
    save_env_value("SHARING_DRIVE_MOUNT", str(drive), env)
    assert "isn't on the external drive" in setup.value_warning("SHARING_DIR", str(tmp_path / "elsewhere"), env)
    # Missing on a mounted drive: may be created
    assert setup.value_warning("SHARING_DIR", str(drive / "Shared"), env) is None


def test_sharing_dir_on_an_unplugged_drive_warns_and_is_never_created(env, tmp_path):
    drive = tmp_path / "drive"  # an empty mount point: not mounted
    drive.mkdir()
    save_env_value("SHARING_DRIVE_MOUNT", str(drive), env)
    assert "isn't a folder" in setup.value_warning("SHARING_DIR", str(drive / "Shared"), env)
    assert not setup.can_create_sharing_dir(str(drive / "Shared"), env)


def test_only_the_library_may_be_a_network_folder(env):
    assert setup.value_warning("MUSIC_LIBRARY_DIR", "smb://nas/music/", env) is None
    assert "must be a local folder" in setup.value_warning("SHARING_DIR", "smb://nas/shared/", env)


def test_sharing_dir_must_be_writable(env, tmp_path, monkeypatch):
    monkeypatch.setattr(setup.os, "access", lambda path, mode: False)
    assert "can't be written" in setup.value_warning("SHARING_DIR", str(tmp_path), env)


# ==================== suggestions ====================

def test_sharing_dir_suggestion_follows_the_drive(env, tmp_path):
    assert setup.suggestion("SHARING_DIR", env, home=tmp_path) == str(tmp_path / "MusicDatabase-Shared")
    save_env_value("SHARING_DRIVE_MOUNT", "/media/me/Data", env)
    assert setup.suggestion("SHARING_DIR", env, home=tmp_path) == "/media/me/Data/MusicDatabase-Shared"


def test_lan_suggestion_only_when_there_is_one_interface(env):
    one = lambda: [("enp2s0", "192.0.2.10")]
    two = lambda: [("enp2s0", "192.0.2.10"), ("wlp3s0", "192.0.2.11")]
    assert setup.suggestion("SHARING_LAN_INTERFACE", env, interfaces=one) == "enp2s0"
    assert setup.suggestion("SHARING_LAN_INTERFACE", env, interfaces=two) is None


def test_library_suggestion_is_the_music_folder(env, tmp_path):
    assert setup.suggestion("MUSIC_LIBRARY_DIR", env, home=tmp_path) is None
    (tmp_path / "Music").mkdir()
    assert setup.suggestion("MUSIC_LIBRARY_DIR", env, home=tmp_path) == str(tmp_path / "Music")


def test_lan_interfaces_leave_out_loopback_bridges_and_tailscale(tmp_path):
    for name in ("lo", "docker0", "br-1a2b", "veth9", "tailscale0", "enp2s0", "wlp3s0"):
        (tmp_path / name).mkdir()
    addresses = {"enp2s0": "192.0.2.10", "docker0": "172.17.0.1"}
    found = setup.lan_interfaces(str(tmp_path), address_of=addresses.get)
    assert found == [("enp2s0", "192.0.2.10")]


def test_mounted_drives(tmp_path, fake_mounts):
    media = tmp_path / "media"
    (media / "me" / "Data").mkdir(parents=True)
    (media / "me" / "empty").mkdir()
    fake_mounts.add(str(media / "me" / "Data"))
    assert setup.mounted_drives("me", roots=(str(media),)) == [str(media / "me" / "Data")]


def test_restart_needed_compares_with_what_the_app_started_with(env):
    save_env_value("SHARING_DIR", "/srv/shared", env)
    assert setup.restart_needed(env, environ={}) == ["SHARING_DIR"]
    assert setup.restart_needed(env, environ={"SHARING_DIR": "/srv/shared"}) == []


# ==================== checks ====================

class _Out:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


LOCAL = lambda: [("enp2s0", "192.0.2.10"), ("wg0", "10.8.0.2")]


def test_vpn_is_tailscale_off_or_an_existing_vpn_interface(env, monkeypatch):
    assert setup.value_warning("SHARING_VPN", "tailscale", env) is None
    assert setup.value_warning("SHARING_VPN", "OFF", env) is None
    assert "no network interface nosuchvpn0" in setup.value_warning("SHARING_VPN", "nosuchvpn0", env)
    monkeypatch.setattr(setup.os.path, "exists", lambda path: True)
    assert setup.value_warning("SHARING_VPN", "wg0", env) is None
    assert "container bridge" in setup.value_warning("SHARING_VPN", "docker0", env)
    save_env_value("SHARING_LAN_INTERFACE", "enp2s0", env)
    assert "home network interface" in setup.value_warning("SHARING_VPN", "enp2s0", env)


def test_vpn_candidates_are_any_providers_interfaces(env):
    found = lambda: [("enp2s0", "192.0.2.10"), ("wg0", "10.8.0.2"), ("tun0", "10.9.0.6"),
                     ("tailscale0", "100.64.0.10"), ("docker0", "172.17.0.1"), ("ppp0", "8.8.8.8")]
    save_env_value("SHARING_LAN_INTERFACE", "enp2s0", env)
    assert setup.vpn_candidates(env, addresses=found) == [
        ("wg0", "10.8.0.2"), ("tun0", "10.9.0.6"), ("tailscale0", "100.64.0.10")]


def test_addresses_must_be_this_pcs_ipv4_addresses(env):
    assert setup.value_warning("SHARING_ADDRESSES", "10.8.0.2, 192.0.2.10", env, local_addresses=LOCAL) is None
    assert "isn't an IPv4 address" in setup.value_warning("SHARING_ADDRESSES", "home-pc", env, local_addresses=LOCAL)
    assert "isn't an address of this PC" in setup.value_warning("SHARING_ADDRESSES", "198.51.100.7", env,
                                                                local_addresses=LOCAL)
    assert "every network" in setup.value_warning("SHARING_ADDRESSES", "0.0.0.0", env, local_addresses=LOCAL)


def test_local_ipv4_addresses_leave_out_loopback():
    out = ("1: lo    inet 127.0.0.1/8 scope host lo\n"
           "2: enp2s0    inet 192.0.2.10/24 brd 192.0.2.255 scope global enp2s0\n"
           "5: wg0    inet 10.8.0.2/32 scope global wg0\n")
    assert setup.local_ipv4_addresses(run=lambda *a, **k: _Out(out)) == [("enp2s0", "192.0.2.10"), ("wg0", "10.8.0.2")]


def test_address_candidates_never_offer_bridges_or_the_other_steps_addresses(env):
    found = lambda: [("enp2s0", "192.0.2.10"), ("wg0", "10.8.0.2"), ("wg1", "10.9.0.2"), ("docker0", "172.17.0.1"),
                     ("br-1a2b", "172.18.0.1"), ("tailscale0", "100.64.0.10")]
    save_env_value("SHARING_LAN_INTERFACE", "enp2s0", env)
    save_env_value("SHARING_ADDRESSES", "10.9.0.2", env)
    # No VPN chosen: every VPN's address may be added
    assert setup.address_candidates(env, addresses=found) == [("wg0", "10.8.0.2"), ("tailscale0", "100.64.0.10")]
    save_env_value("SHARING_VPN", "tailscale", env)
    assert setup.address_candidates(env, addresses=found) == [("wg0", "10.8.0.2")]
    # With another VPN chosen, its interface is that step's, and Tailscale's is free to add
    save_env_value("SHARING_VPN", "wg0", env)
    assert setup.address_candidates(env, addresses=found) == [("tailscale0", "100.64.0.10")]


def test_listening_plan_follows_the_settings(env):
    tailscale = lambda: "100.64.0.10"
    lan = {"enp2s0": "192.0.2.10"}.get
    vpn = {"wg0": "10.8.0.2"}.get
    # Nothing set: no address at all, even with Tailscale running here
    assert setup.listening_plan(env, tailscale, lan, vpn) == []
    save_env_value("SHARING_VPN", "tailscale", env)
    assert setup.listening_plan(env, lambda: None, lan, vpn) == [("VPN (Tailscale)", None)]
    save_env_value("SHARING_LAN_INTERFACE", "enp2s0", env)
    save_env_value("SHARING_ADDRESSES", "198.51.100.7", env)
    assert setup.listening_plan(env, tailscale, lan, vpn) == [
        ("home network (enp2s0)", "192.0.2.10"), ("VPN (Tailscale)", "100.64.0.10"),
        ("chosen address", "198.51.100.7")]
    save_env_value("SHARING_VPN", "wg0", env)
    assert setup.listening_plan(env, tailscale, lan, vpn) == [
        ("home network (enp2s0)", "192.0.2.10"), ("VPN (wg0)", "10.8.0.2"), ("chosen address", "198.51.100.7")]
    save_env_value("SHARING_VPN", "off", env)
    assert setup.listening_plan(env, tailscale, lan, vpn) == [
        ("home network (enp2s0)", "192.0.2.10"), ("chosen address", "198.51.100.7")]


def test_linger_state_and_enabling():
    assert setup.linger_enabled(run=lambda *a, **k: _Out("yes\n"), user="me") is True
    assert setup.linger_enabled(run=lambda *a, **k: _Out("no\n"), user="me") is False
    assert setup.linger_enabled(run=lambda *a, **k: _Out(""), user="me") is None
    assert setup.enable_linger(run=lambda *a, **k: _Out()) is None
    assert "code 1" in setup.enable_linger(run=lambda *a, **k: _Out(returncode=1))


def test_service_active():
    assert setup.service_active("x", run=lambda *a, **k: _Out("active\n"))
    assert not setup.service_active("x", run=lambda *a, **k: _Out("inactive\n"))


class _Opened:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("raised, expected", [
    (None, setup.SERVING),
    (urllib.error.HTTPError("u", 404, "", {}, None), setup.NO_COPY),
    (urllib.error.HTTPError("u", 503, "", {}, None), setup.DATA_MISSING),
    (urllib.error.URLError("refused"), setup.NOT_ANSWERING),
])
def test_server_answer(raised, expected):
    seen = []

    def opener(request, timeout):
        seen.append((request.full_url, request.get_method()))
        if raised:
            raise raised
        return _Opened()

    assert setup.server_answer("100.64.0.10", 8002, opener=opener) == expected
    assert seen == [("http://100.64.0.10:8002/music.db", "HEAD")]


def test_phone_addresses():
    plan = [("home network (enp2s0)", "192.0.2.10"), ("Tailscale", "100.64.0.10"),
            ("chosen address", "0.0.0.0"), ("home network (wlp3s0)", None)]
    assert setup.phone_addresses(plan, 8002) == [
        ("Adres serwera (sieć lokalna)", "http://192.0.2.10:8002/"),
        ("Adres serwera (połączenie zdalne)", "http://100.64.0.10:8002/"),
    ]
    assert setup.phone_addresses([]) == []


# ==================== the wizard's flow ====================

@pytest.fixture
def wizard(monkeypatch, tmp_path):
    """Scripted answers: `selects` are values picked, `texts` typed, `yes`
    the yes/no answers. Records what each select offered, the yes/no
    questions asked and whether the checks / restart ran."""
    state = {"selects": [], "texts": [], "yes": [], "offered": [], "asked": [], "checks": 0, "restarted": 0}

    def select(message, choices):
        state["offered"].append([(c.title, c.value) for c in choices])
        return state["selects"].pop(0)

    def yes(message):
        state["asked"].append(message)
        return state["yes"].pop(0)

    monkeypatch.setattr(wiz, "_select", select)
    monkeypatch.setattr(wiz, "_text", lambda message, default="": state["texts"].pop(0))
    monkeypatch.setattr(wiz, "ask_yes_no", yes)
    monkeypatch.setattr(wiz, "_checks", lambda env_file: state.__setitem__("checks", state["checks"] + 1))
    monkeypatch.setattr(wiz, "restart_to_continue", lambda: state.__setitem__("restarted", state["restarted"] + 1))
    monkeypatch.setattr(setup, "mounted_drives", lambda: [])
    monkeypatch.setattr(setup, "lan_interfaces", lambda: [("enp2s0", "192.0.2.10")])
    monkeypatch.setattr(setup, "local_ipv4_addresses", LOCAL)
    monkeypatch.setattr(setup, "listening_plan", lambda env_file: [("home network (enp2s0)", "192.0.2.10")])
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # What this process "started with": nothing set
    for step in setup.SETTING_STEPS:
        monkeypatch.delenv(step.name, raising=False)
    return state


KEEP = wiz._KEEP
STEPS = len(setup.SETTING_STEPS)


def _keep_rest(done: int) -> list:
    return [KEEP] * (STEPS - done)


def _step(name: str) -> int:
    return [step.name for step in setup.SETTING_STEPS].index(name)


def test_keeping_everything_changes_nothing_and_asks_nothing(wizard, env):
    before = env.read_bytes()
    wizard["selects"] = _keep_rest(0)

    wiz.run_sharing_wizard(env_file=env)

    assert env.read_bytes() == before and wizard["asked"] == []
    assert wizard["checks"] == 1 and wizard["restarted"] == 0


def test_a_picked_value_is_saved_only_when_confirmed(wizard, env, tmp_path):
    before = env.read_bytes()
    # Typed, "Save it?" no, asked the step again, kept
    wizard["selects"] = [KEEP, wiz._TYPE, KEEP] + _keep_rest(2)
    wizard["texts"] = [str(tmp_path)]
    wizard["yes"] = [False]

    wiz.run_sharing_wizard(env_file=env)

    assert wizard["asked"] == [f"Save SHARING_DIR={tmp_path} to .env?"]
    assert env.read_bytes() == before and wizard["checks"] == 1


def test_stopping_keeps_what_was_saved_and_touches_nothing_else(wizard, env, tmp_path):
    wizard["selects"] = [KEEP, wiz._TYPE, wiz._STOP]
    wizard["texts"] = [str(tmp_path)]  # an existing folder: no warning, no "create it?"
    wizard["yes"] = [True]

    wiz.run_sharing_wizard(env_file=env)

    assert read_env_value("SHARING_DIR", env) == str(tmp_path)
    assert read_env_value("SMB_USERNAME", env) == "someone"
    assert read_env_value("SHARING_LAN_INTERFACE", env) == ""
    assert wizard["checks"] == 0 and wizard["restarted"] == 0


def test_ctrl_c_at_any_prompt_stops_like_stop(wizard, env):
    before = env.read_bytes()
    wizard["selects"] = [KEEP, None]

    wiz.run_sharing_wizard(env_file=env)

    assert env.read_bytes() == before and wizard["checks"] == 0


def test_ctrl_c_at_a_question_stops_without_saving(wizard, env, tmp_path):
    before = env.read_bytes()
    wizard["selects"] = [KEEP, wiz._TYPE]
    wizard["texts"] = [str(tmp_path)]
    wizard["yes"] = [None]

    wiz.run_sharing_wizard(env_file=env)

    assert env.read_bytes() == before and wizard["checks"] == 0


def test_new_values_are_saved_and_a_restart_is_offered(wizard, env):
    wizard["selects"] = [KEEP, wiz._SUGGESTED, "enp2s0"] + _keep_rest(3)
    wizard["yes"] = [True, True, True, True]  # save the folder, create it, save the interface, restart

    wiz.run_sharing_wizard(env_file=env)

    shared = os.path.join(os.environ["HOME"], "MusicDatabase-Shared")
    assert read_env_value("SHARING_DIR", env) == shared and os.path.isdir(shared)
    assert read_env_value("SHARING_LAN_INTERFACE", env) == "enp2s0"
    assert wizard["asked"][1] == f"{shared} doesn't exist yet. Create it?"
    assert wizard["restarted"] == 1 and wizard["checks"] == 0


def test_a_declined_folder_isnt_created(wizard, env):
    wizard["selects"] = [KEEP, wiz._SUGGESTED] + _keep_rest(2)
    wizard["yes"] = [True, False, False]  # save, don't create, don't restart

    wiz.run_sharing_wizard(env_file=env)

    assert read_env_value("SHARING_DIR", env)
    assert not os.path.exists(read_env_value("SHARING_DIR", env))
    assert wizard["restarted"] == 0 and wizard["checks"] == 0


def test_running_again_shows_the_saved_values(wizard, env, tmp_path, monkeypatch):
    save_env_value("SHARING_DIR", str(tmp_path), env)
    save_env_value("SHARING_LAN_INTERFACE", "enp2s0", env)
    monkeypatch.setenv("SHARING_DIR", str(tmp_path))
    monkeypatch.setenv("SHARING_LAN_INTERFACE", "enp2s0")
    wizard["selects"] = _keep_rest(0)

    wiz.run_sharing_wizard(env_file=env)

    dir_offer, lan_offer = wizard["offered"][_step("SHARING_DIR")], wizard["offered"][_step("SHARING_LAN_INTERFACE")]
    assert dir_offer[0] == (f"Keep it: {tmp_path}", KEEP)
    assert lan_offer[0] == ("Keep it: enp2s0", KEEP)
    # The interface already set isn't offered again, an optional value can be cleared
    assert ("This interface: enp2s0 (192.0.2.10)", "enp2s0") not in lan_offer
    assert any(value == wiz._CLEAR for _, value in lan_offer)
    assert wizard["asked"] == [] and wizard["checks"] == 1


def test_the_required_sharing_folder_cant_be_cleared(wizard, env, tmp_path, monkeypatch):
    save_env_value("SHARING_DIR", str(tmp_path), env)
    monkeypatch.setenv("SHARING_DIR", str(tmp_path))
    wizard["selects"] = [KEEP, wiz._TYPE, KEEP] + _keep_rest(2)
    wizard["texts"] = [""]

    wiz.run_sharing_wizard(env_file=env)

    assert all(value != wiz._CLEAR for _, value in wizard["offered"][1])
    # The empty value was refused without a question and the step asked again
    assert len(wizard["offered"]) == STEPS + 1 and wizard["asked"] == []
    assert read_env_value("SHARING_DIR", env) == str(tmp_path)


def test_a_value_with_a_warning_is_saved_only_when_confirmed(wizard, env, capsys):
    lan = _step("SHARING_LAN_INTERFACE")
    wizard["selects"] = [KEEP] * lan + [wiz._TYPE] + _keep_rest(lan + 1)
    wizard["texts"] = ["nosuchif0"]
    wizard["yes"] = [True, False]  # save, no restart now

    wiz.run_sharing_wizard(env_file=env)

    assert "There's no network interface nosuchif0" in capsys.readouterr().out
    assert read_env_value("SHARING_LAN_INTERFACE", env) == "nosuchif0"


def test_the_vpn_step_offers_any_providers_interface_tailscale_and_off(wizard, env, monkeypatch):
    monkeypatch.setattr(setup.os.path, "exists", lambda path: True)
    step = _step("SHARING_VPN")
    wizard["selects"] = [KEEP] * step + ["wg0"] + _keep_rest(step + 1)
    wizard["yes"] = [True, False]

    wiz.run_sharing_wizard(env_file=env)

    offered = [value for _, value in wizard["offered"][step]]
    assert "wg0" in offered and "tailscale" in offered and "off" in offered and wiz._TYPE in offered
    assert read_env_value("SHARING_VPN", env) == "wg0"


def test_an_address_is_added_to_the_ones_already_chosen(wizard, env, monkeypatch):
    save_env_value("SHARING_ADDRESSES", "192.0.2.10", env)
    monkeypatch.setenv("SHARING_ADDRESSES", "192.0.2.10")
    step = _step("SHARING_ADDRESSES")
    wizard["selects"] = [KEEP] * step + [wiz._ADD + "10.8.0.2"] + _keep_rest(step + 1)
    wizard["yes"] = [True, False]

    wiz.run_sharing_wizard(env_file=env)

    offered = wizard["offered"][step]
    # An address already chosen isn't offered again
    assert ("Add this address: 192.0.2.10 (enp2s0)", wiz._ADD + "192.0.2.10") not in offered
    assert read_env_value("SHARING_ADDRESSES", env) == "192.0.2.10,10.8.0.2"


def test_no_address_to_listen_on_is_pointed_out(wizard, env, monkeypatch, capsys):
    monkeypatch.setattr(setup, "listening_plan", lambda env_file: [])
    wizard["selects"] = _keep_rest(0)

    wiz.run_sharing_wizard(env_file=env)

    assert "no address to listen on" in capsys.readouterr().out


def test_resuming_after_the_restart_goes_straight_to_the_checks(wizard, env):
    wiz.run_sharing_wizard(resume=True, env_file=env)
    assert wizard["checks"] == 1 and wizard["offered"] == []


def test_a_missing_folder_on_an_unplugged_drive_is_never_created(env, tmp_path, monkeypatch):
    drive = tmp_path / "drive"
    drive.mkdir()
    save_env_value("SHARING_DRIVE_MOUNT", str(drive), env)
    monkeypatch.setattr(wiz, "ask_yes_no", lambda message: pytest.fail("asked to create it"))
    wiz.offer_to_create_sharing_dir(str(drive / "Shared"), env)
    assert not (drive / "Shared").exists()


# ==================== the checks: nothing done without a yes ====================

def test_a_declined_first_copy_is_listed_as_not_done(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from utils.database import sharing

    monkeypatch.setattr(wiz, "ask_yes_no", lambda message: False)
    monkeypatch.setattr(sharing, "share_databases", lambda: pytest.fail("copied without a yes"))
    report = wiz.Report()
    wiz._check_first_copy(SimpleNamespace(sharing_dir=tmp_path), report)
    assert report.not_done and "first copy" in report.not_done[0]


def test_a_declined_service_isnt_installed(tmp_path, monkeypatch, env):
    from utils.database import sharing

    monkeypatch.setattr(wiz, "ask_yes_no", lambda message: False)
    monkeypatch.setattr(sharing, "service_unit_current", lambda: False)
    monkeypatch.setattr(sharing, "service_unit_path", lambda: tmp_path / "missing.service")
    monkeypatch.setattr(sharing, "install_sharing_service", lambda: pytest.fail("installed without a yes"))
    monkeypatch.setattr(setup, "service_active", lambda name: False)
    monkeypatch.setattr(setup, "listening_plan", lambda env_file: [("home network (enp2s0)", "192.0.2.10")])
    report = wiz.Report()
    wiz._check_service(env, report)
    assert report.not_done and "sharing server" in report.not_done[0]


def test_the_service_isnt_offered_without_an_address(tmp_path, monkeypatch, env):
    from utils.database import sharing

    monkeypatch.setattr(wiz, "ask_yes_no", lambda message: pytest.fail("offered with no address"))
    monkeypatch.setattr(sharing, "service_unit_current", lambda: False)
    monkeypatch.setattr(sharing, "service_unit_path", lambda: tmp_path / "missing.service")
    monkeypatch.setattr(setup, "service_active", lambda name: False)
    monkeypatch.setattr(setup, "listening_plan", lambda env_file: [])
    report = wiz.Report()
    wiz._check_service(env, report)
    assert "no address" in report.not_done[0]


def test_an_install_refused_for_no_address_is_listed_as_not_done(tmp_path, monkeypatch, env):
    # .env has an address, the settings the unit is written from don't
    from utils.database import sharing

    def no_address():
        raise sharing.NoListenAddressError("no address")

    monkeypatch.setattr(wiz, "ask_yes_no", lambda message: True)
    monkeypatch.setattr(sharing, "service_unit_current", lambda: False)
    monkeypatch.setattr(sharing, "service_unit_path", lambda: tmp_path / "missing.service")
    monkeypatch.setattr(sharing, "install_sharing_service", no_address)
    monkeypatch.setattr(setup, "service_active", lambda name: False)
    monkeypatch.setattr(setup, "listening_plan", lambda env_file: [("home network (enp2s0)", "192.0.2.10")])
    report = wiz.Report()
    wiz._check_service(env, report)
    assert "no address" in report.not_done[0]


def test_declined_lingering_is_listed_with_the_command(monkeypatch):
    monkeypatch.setattr(wiz, "ask_yes_no", lambda message: False)
    monkeypatch.setattr(setup, "linger_enabled", lambda: False)
    monkeypatch.setattr(setup, "enable_linger", lambda: pytest.fail("enabled without a yes"))
    report = wiz.Report()
    wiz._check_linger(report)
    assert "loginctl enable-linger" in report.not_done[0]


def test_the_summary_lists_what_wasnt_done(env, monkeypatch, capsys):
    monkeypatch.setattr(setup, "listening_plan", lambda env_file: [("home network (enp2s0)", "192.0.2.10")])
    monkeypatch.setattr(setup, "server_answer", lambda address: setup.NOT_ANSWERING)
    report = wiz.Report()
    report.skip("the sharing server (Settings -> Server -> Setup this PC for database sharing)")
    wiz._summary(env, report)
    out = capsys.readouterr().out
    assert "Not done:" in out and "the sharing server" in out
    assert "Adres serwera (sieć lokalna): http://192.0.2.10:8002/" in out
    assert "Tailscale" not in out


def test_setup_database_sharing_offers_the_wizard_first(monkeypatch):
    from menu.main_menu import settings as settings_menu

    ran = []
    monkeypatch.setattr(settings_menu, "ask_yes_no", lambda message: True)
    monkeypatch.setattr(settings_menu, "run_sharing_wizard", lambda: ran.append(True))
    monkeypatch.setattr(settings_menu, "install_sharing_service", lambda: pytest.fail("installed without asking"))
    settings_menu.setup_database_sharing()
    assert ran == [True]


def test_setup_without_the_wizard_needs_a_restart_after_a_change(monkeypatch):
    # .env changed since start-up: the unit would be written from the old settings
    from menu.main_menu import settings as settings_menu

    answers = iter([False, False])
    monkeypatch.setattr(settings_menu, "ask_yes_no", lambda message: next(answers))
    monkeypatch.setattr(settings_menu, "restart_needed", lambda: ["SHARING_LAN_INTERFACE"])
    monkeypatch.setattr(settings_menu, "restart_to_continue", lambda: pytest.fail("restarted without a yes"))
    monkeypatch.setattr(settings_menu, "install_sharing_service", lambda: pytest.fail("installed on old settings"))
    settings_menu.setup_database_sharing()


def test_setup_without_the_wizard_restarts_on_a_yes(monkeypatch):
    from menu.main_menu import settings as settings_menu

    restarted = []
    answers = iter([False, True])
    monkeypatch.setattr(settings_menu, "ask_yes_no", lambda message: next(answers))
    monkeypatch.setattr(settings_menu, "restart_needed", lambda: ["SHARING_LAN_INTERFACE"])
    monkeypatch.setattr(settings_menu, "restart_to_continue", lambda: restarted.append(True))
    monkeypatch.setattr(settings_menu, "install_sharing_service", lambda: pytest.fail("installed on old settings"))
    settings_menu.setup_database_sharing()
    assert restarted == [True]
