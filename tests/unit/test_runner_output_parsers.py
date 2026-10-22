"""Unmodified, attributed clean-runner output exercises real parser boundaries."""

import json
from pathlib import Path

from travel_mode.config import Config
from travel_mode.flows.report import parse_darkwake_reasons, parse_sleep_segments
from travel_mode.live_power import sleep_events
from travel_mode.sessions import observe
from travel_mode.system import battery, health, launchd, network, platform, pmset, process_control, spotlight
from travel_mode.system.lsappinfo import foreground
from travel_mode.system.shell import CmdResult

FIXTURE = Path(__file__).parents[1] / "fixtures/macos-26-arm64"


def text(name):
    return (FIXTURE / f"{name}.stdout").read_text()


def install(fake):
    data = json.loads((FIXTURE / "provenance.json").read_text())
    for name, row in data["commands"].items():
        fake.responses[tuple(row["command"])] = CmdResult(
            text(name), (FIXTURE / f"{name}.stderr").read_text(), row["rc"]
        )


def test_runner_vm_power_capabilities_do_not_invent_battery_or_sleep_switch(fake_runner):
    install(fake_runner)
    assert pmset.parse_sleep_disabled(text("pmset-current")) is None
    profiles = pmset.pmset_custom()
    assert set(profiles) == {"a"}
    assert profiles["a"]["displaysleep"] == "0" and profiles["a"]["powernap"] == "1"
    supported = platform.supported_pmset_keys()
    assert {"displaysleep", "powernap", "sleep", "tcpkeepalive"} <= supported
    assert not {"lowpowermode", "powermode", "hibernatemode"} & supported
    assert pmset.on_ac_power() is True
    assert battery.parse(text("ioreg-battery")) == {"available": False, "reason": "no_battery"}
    reading = observe()
    assert reading["percent"] is None and reading["remaining_mah"] is None
    assert reading["errors"]  # AC-only output cannot synthesize a charge reading.


def test_runner_foreground_and_both_process_layouts_preserve_process_identity(fake_runner):
    install(fake_runner)
    front = foreground()
    assert (front.pid, front.bundle_id) == (350, "com.apple.finder")
    identity = next(row for row in process_control.process_table() if row.pid == front.pid)
    cpu = next(row for row in health.parse_processes(text("ps-health")) if row["pid"] == front.pid)
    assert (
        identity.command
        == cpu["executable"]
        == "/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder"
    )
    assert identity.started == cpu["started"]
    assert identity.ppid == cpu["ppid"] == 1


def test_runner_services_assertions_and_readonly_statuses(fake_runner):
    install(fake_runner)
    loaded = launchd._loaded(text("launchctl-list").splitlines())
    assert loaded["com.apple.Finder"][0] == "350"
    assert loaded["com.apple.Dock.agent"][0] == "346"
    assertions = health.Session(Config.defaults(), lambda: 0).assertions()
    assert {row["pid"] for row in assertions} == {"99", "300"}
    assert all(row["scope"] == "idle" for row in assertions)
    assert spotlight.spotlight_on() is True
    assert launchd.time_machine_info()["running"] is False
    assert network.wifi_dev() == ""  # This VM exposes only Ethernet ports.
    assert sleep_events(text("sleep-times")) == (None, None)
    assert parse_sleep_segments(text("pmset-log")) == []
    assert parse_darkwake_reasons(text("pmset-log")) == {}
