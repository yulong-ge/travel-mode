"""Safety regressions use command doubles and isolated state only."""

import json
import plistlib
import shutil

import pytest

import travel_mode.flows.apply as flow_apply
import travel_mode.flows.restore as flow_restore
import travel_mode.network_apps as network_api
import travel_mode.snapshots as snapshots
import travel_mode.state as state_api
from travel_mode import config as config_module
from travel_mode import state
from travel_mode.config import load_config
from travel_mode.system import apps as system_apps
from travel_mode.system import brightness, daemon_apps, launchd, network, pmset, processes, spotlight
from travel_mode.system.shell import CmdResult


def quiet_apply(monkeypatch):
    monkeypatch.setattr(processes, "caffeinate_procs", lambda: [])
    monkeypatch.setattr(pmset, "configured_sleep_blocking_assertions", lambda: [])
    monkeypatch.setattr(pmset, "audio_assertion", lambda: "")
    monkeypatch.setattr(system_apps, "scan_apps", lambda: {"running": [], "nobundle": []})
    monkeypatch.setattr(launchd, "svc_scan", lambda: {"unloadable": []})
    monkeypatch.setattr(spotlight, "spotlight_on", lambda: False)


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, RuntimeError])
def test_power_receipt_survives_interruption(monkeypatch, fake_runner, tmp_paths, interruption):
    quiet_apply(monkeypatch)
    monkeypatch.setattr(pmset, "sleep_disabled", lambda: "0")
    monkeypatch.setattr(pmset, "power_recommendations", lambda profiles=None: [("sleep", "sleep", "5")])
    original = pmset.pmset_custom
    reads = 0

    def profiles():
        nonlocal reads
        reads += 1
        if reads == 3:
            raise interruption("after mutation")
        return original()

    monkeypatch.setattr(pmset, "pmset_custom", profiles)
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    with pytest.raises(interruption):
        flow_apply.mode_apply(yes=True)
    persisted = state.load_state()
    assert persisted is not None
    assert "sleep" in persisted["batt_prof"]
    assert persisted["ac_prof"] == ""
    assert persisted["sleepdisabled"] == ""


def test_snapshot_app_only_does_not_restore_power(monkeypatch, fake_runner):
    snapshot, _ = snapshots.snapshot_origin()
    snapshot["quit_apps"] = ["/Demo.app"]
    fake_runner.responses[("sudo", "-n", "launchctl", "list")] = CmdResult("PID Status Label\n", "", 0)
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    fake_runner.responses[("open",)] = CmdResult("", "", 0)
    flow_restore.run_restore(snapshot, lambda *_: None)
    assert not any("pmset" in call and "-g" not in call for call in fake_runner.calls)


def test_powermode_origin_is_kept(fake_runner):
    fake_runner.responses[("pmset", "-g", "custom")] = CmdResult(
        "Battery Power:\n powermode 2\nAC Power:\n powermode 0\n", "", 0
    )
    assert "powermode 2" in state_api.profile_string(pmset.pmset_custom(), "b")


def test_ac_restores_ac_domain(monkeypatch, fake_runner):
    monkeypatch.setattr(pmset, "_profile_args", lambda value: value.split())
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    fake_runner.responses[("sudo", "-n", "launchctl", "list")] = CmdResult("", "", 0)
    snapshot = state.new_state()
    snapshot["ac_prof"] = "sleep 0"
    flow_restore.run_restore(snapshot, lambda *_: None)
    assert ("sudo", "pmset", "-a", "sleep", "0") not in fake_runner.calls
    assert ("sudo", "pmset", "-c", "sleep", "0") in fake_runner.calls
    # pmset parser uses 'a' internally for AC; CLI -c is AC (macOS documented).


@pytest.mark.parametrize(
    "probe", ["pmset_custom", "sleep_disabled", "wifi_powered", "bt_powered", "spotlight_on"]
)
def test_failed_critical_probe_is_error(fake_runner, probe):
    for key in list(fake_runner.responses):
        if key[0] in {"pmset", "networksetup", "blueutil", "mdutil"}:
            fake_runner.responses[key] = CmdResult("", "timeout", 124)
    with pytest.raises(RuntimeError, match="124"):
        getattr(
            network
            if probe in {"wifi_powered", "bt_powered"}
            else spotlight
            if probe == "spotlight_on"
            else pmset,
            probe,
        )(*("en0",) if probe == "wifi_powered" else ())


def test_missing_sleepdisabled_does_not_synthesize_zero(fake_runner):
    fake_runner.responses[("pmset", "-g")] = CmdResult("System-wide power settings:\n", "", 0)
    assert pmset.sleep_disabled() is None


def test_restore_matches_exact_plist_label(tmp_paths, fake_runner):
    plist = tmp_paths / "arbitrary-name.plist"
    plist.write_bytes(plistlib.dumps({"Label": "com.demo.worker"}))
    fake_runner.responses[("launchctl", "list")] = CmdResult("- 0 com.demo.worker-helper\n", "", 0)
    fake_runner.responses[("sudo", "-n", "launchctl", "list")] = CmdResult("", "", 0)
    fake_runner.responses[("launchctl", "load")] = CmdResult("", "", 0)
    snapshot = state.new_state()
    snapshot["unloaded_svcs"] = [str(plist)]
    # Basename equal to a loaded service also must not hide a different Label.
    other = tmp_paths / "com.demo.worker-helper.plist"
    plist.rename(other)
    snapshot["unloaded_svcs"] = [str(other)]
    assert flow_restore.run_restore(snapshot, lambda *_: None) == (1, 0)
    assert ("launchctl", "load", str(other)) in fake_runner.calls


def test_restore_unsupported_profile_retains_failure(monkeypatch, fake_runner):
    monkeypatch.setattr(pmset, "_profile_args", lambda value: [])
    fake_runner.responses[("sudo", "-n", "launchctl", "list")] = CmdResult("", "", 0)
    snapshot = state.new_state()
    snapshot["batt_prof"] = "hibernatemode 3"
    assert flow_restore.run_restore(snapshot, lambda *_: None) == (0, 1)
    assert snapshot["batt_prof"] == "hibernatemode 3"


def test_partial_restore_checkpoints_success(fake_runner):
    fake_runner.responses[("networksetup", "-setairportpower")] = CmdResult("", "", 0)
    fake_runner.responses[("blueutil", "-p")] = CmdResult("failed", "", 2)
    fake_runner.responses[("sudo", "networksetup")] = CmdResult("", "", 0)
    fake_runner.responses[("sudo", "-n", "launchctl", "list")] = CmdResult("", "", 0)
    snapshot = state.new_state()
    snapshot.update(wifi_off="en0", bt_off="1")
    state.save_state(snapshot)
    assert flow_restore.run_restore(snapshot, lambda *_: None) == (1, 1)
    persisted = state.load_state()
    assert persisted["wifi_off"] == ""
    assert persisted["bt_off"] == "1"
    flow_restore.run_restore(persisted, lambda *_: None)
    assert sum("networksetup" in cmd for cmd in fake_runner.calls) == 1


@pytest.mark.parametrize("bad", [{"frozen_apps": [{}]}, {"sleepdisabled": None}, {"wifi_off": 3}])
def test_malformed_state_fails_at_read(tmp_paths, bad):
    path = tmp_paths / "state.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(state.StateError):
        state.load_state()


@pytest.mark.parametrize(
    "text", ['[services]\nunload=[""]', '[apps]\nfreeze=["   "]', '[[system_apps.with_daemon]]\nname=""']
)
def test_empty_config_selectors_rejected(tmp_paths, text):
    path = tmp_paths / "config.toml"
    path.write_text(text)
    with pytest.raises(config_module.ConfigError):
        load_config(path)


def test_logical_lowpowermode_override_survives_mapping(monkeypatch, tmp_paths, fake_runner):
    (tmp_paths / "config.toml").write_text("[power.battery]\nlowpowermode=0\n")
    monkeypatch.setattr("travel_mode.system.platform.supported_pmset_keys", lambda: frozenset({"powermode"}))
    fake_runner.responses[("pmset", "-g", "custom")] = CmdResult("Battery Power:\n powermode 2\n", "", 0)
    assert pmset.power_recommendations()[0][::2] == ("powermode", "0")


def test_recovery_intent_precedes_first_mutation(monkeypatch, fake_runner):
    quiet_apply(monkeypatch)
    monkeypatch.setattr(pmset, "sleep_disabled", lambda: "0")
    monkeypatch.setattr(pmset, "power_recommendations", lambda profiles=None: [("sleep", "sleep", "5")])
    original_runner = fake_runner.__call__
    observed = []

    def runner(command, timeout=None, interactive=False):
        if command[:3] == ["sudo", "pmset", "-b"]:
            persisted = state.load_state()
            assert persisted is not None, "system mutation ran before recovery intent was saved"
            assert persisted["batt_prof"]
            observed.append(persisted)
            return CmdResult("", "", 0)
        return original_runner(command, timeout, interactive)

    from travel_mode.system.shell import set_runner

    set_runner(runner)
    with pytest.raises(RuntimeError, match="after mutation"):
        original = pmset.pmset_custom
        reads = 0

        def profiles():
            nonlocal reads
            reads += 1
            if reads == 3:
                raise RuntimeError("after mutation")
            return original()

        monkeypatch.setattr(pmset, "pmset_custom", profiles)
        flow_apply.mode_apply(yes=True)
    assert observed


def test_failed_journal_write_prevents_system_mutation(monkeypatch, fake_runner):
    quiet_apply(monkeypatch)
    monkeypatch.setattr(pmset, "sleep_disabled", lambda: "0")
    monkeypatch.setattr(pmset, "power_recommendations", lambda profiles=None: [("sleep", "sleep", "5")])
    fake_runner.responses[("sudo", "pmset")] = CmdResult("", "", 0)
    monkeypatch.setattr(brightness, "current_brightness", lambda: "0.8")

    def fail_save(*args, **kwargs):
        raise state.StateError("disk full")

    monkeypatch.setattr(state, "save_state", fail_save)
    with pytest.raises(state.StateError, match="disk full"):
        flow_apply.mode_apply(yes=True)
    assert not any(cmd[:2] == ("sudo", "pmset") for cmd in fake_runner.calls)


def test_fake_on_off_roundtrip_preserves_every_original_and_unmodified_key(monkeypatch):
    """Exercise the actual flows against a stateful command double, not mocked restore helpers."""
    from copy import deepcopy

    from travel_mode.system.shell import set_runner

    original = {
        "b": {
            "sleep": "12",
            "displaysleep": "7",
            "powermode": "2",
            "hibernatemode": "3",
            "powernap": "1",
            "womp": "1",
        },
        "a": {"sleep": "0", "powernap": "1", "womp": "1"},
    }
    profiles = deepcopy(original)
    hardware = {"sleepdisabled": "1", "brightness": "0.8", "wifi": "On", "bt": "1", "spot": "enabled"}
    original_hardware = dict(hardware)
    commands = []

    def runner(command, timeout=None, interactive=False):
        commands.append(tuple(command))
        cmd = command[1:] if command[0] == "sudo" else command
        output = ""
        if cmd == ["-n", "true"] or cmd[:2] == ["-n", "-l"]:
            output = "NOPASSWD: ALL"
        elif cmd == ["pmset", "-g", "custom"]:
            output = "\n".join(
                f"{title}:\n" + "\n".join(f" {key} {value}" for key, value in profiles[domain].items())
                for domain, title in (("b", "Battery Power"), ("a", "AC Power"))
            )
        elif cmd == ["pmset", "-g", "cap"]:
            output = "\n".join(profiles["b"])
        elif cmd == ["pmset", "-g"]:
            output = "SleepDisabled " + hardware["sleepdisabled"]
        elif cmd == ["pmset", "-g", "assertions"]:
            output = "Assertion status system-wide:\n"
        elif cmd == ["pmset", "-g", "batt"]:
            output = "InternalBattery 80%;\n"
        elif cmd == ["ioreg", "-rn", "AppleSmartBattery"]:
            output = (
                '"ExternalConnected" = No\n"IsCharging" = No\n"Voltage" = 12000\n'
                '"AppleRawCurrentCapacity" = 4000\n'
            )
        elif cmd == ["pmset", "-g", "log"]:
            output = ""
        elif cmd[:2] == ["pmset", "-a"]:
            assert cmd[2] == "disablesleep"
            hardware["sleepdisabled"] = cmd[3]
        elif cmd[0] == "pmset" and cmd[1] in {"-b", "-c"}:
            domain = "b" if cmd[1] == "-b" else "a"
            profiles[domain].update(zip(cmd[2::2], cmd[3::2], strict=True))
        elif cmd == ["brightness", "-l"]:
            output = "brightness " + hardware["brightness"]
        elif cmd[0] == "brightness":
            hardware["brightness"] = cmd[1]
        elif cmd == ["networksetup", "-listallhardwareports"]:
            output = "Hardware Port: Wi-Fi\nDevice: en0\n"
        elif cmd[:2] == ["networksetup", "-getairportpower"]:
            output = "Wi-Fi Power (en0): " + hardware["wifi"]
        elif cmd[:2] == ["networksetup", "-setairportpower"]:
            hardware["wifi"] = cmd[3].title()
        elif cmd == ["blueutil", "-p"]:
            output = hardware["bt"]
        elif cmd[:2] == ["blueutil", "-p"]:
            hardware["bt"] = cmd[2]
        elif cmd == ["mdutil", "-s", "/"]:
            output = "Indexing " + hardware["spot"] + ".\n"
        elif cmd[:2] == ["mdutil", "-i"]:
            hardware["spot"] = "enabled" if cmd[2] == "on" else "disabled"
        elif cmd == ["pmset", "displaysleepnow"]:
            pass
        else:
            pytest.fail(f"unexpected lifecycle command: {cmd}")
        return CmdResult(output, "", 0)

    monkeypatch.setattr(processes, "caffeinate_procs", lambda: [])
    monkeypatch.setattr(pmset, "configured_sleep_blocking_assertions", lambda: [])
    monkeypatch.setattr(pmset, "audio_assertion", lambda: "")
    monkeypatch.setattr(system_apps, "scan_apps", lambda: {"running": [], "nobundle": []})
    monkeypatch.setattr(launchd, "svc_scan", lambda: {"unloadable": []})
    monkeypatch.setattr(network_api, "configured_names", lambda: ())
    monkeypatch.setattr(shutil, "which", lambda _: "/fake/installed")
    monkeypatch.setattr("travel_mode.system.brightness.available", lambda: True)
    set_runner(runner)
    assert flow_apply.mode_apply(yes=True, aggressive=True) == 0
    assert profiles != original and hardware != original_hardware
    # A setting changed by the user after on must not be reverted by off.
    profiles["a"]["sleep"] = "42"
    assert flow_restore.mode_off() == 0
    expected = deepcopy(original)
    expected["a"]["sleep"] = "42"
    assert profiles == expected
    assert hardware == original_hardware
    assert state.load_state() is None
    assert ("sudo", "pmset", "-c", "powernap", "1", "womp", "1") in commands


def test_configured_daemon_helper_handles_all_network_specs(monkeypatch):
    specs = tuple(config_module.DaemonApp(name) for name in ("First", "Second", "Third"))
    config = config_module.Config(apps=config_module.AppsConfig(with_daemon=specs))
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    called = []

    def quit_spec(spec, snapshot, save=None):
        called.append(spec.name)
        return "", spec.name != "Second", spec.name

    monkeypatch.setattr(daemon_apps, "quit_with_daemons", quit_spec)
    _, ok, message = daemon_apps.quit_with_configured_daemon(state.new_state())
    assert called == ["First", "Second", "Third"]
    assert not ok
    assert all(spec.name in message for spec in specs)
