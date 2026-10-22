"""Unmodified, attributed clean-runner output exercises real parser boundaries."""

import json
import plistlib
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

    fake.responses[tuple(battery.COMMAND)] = CmdResult(plistlib.dumps([]).decode(), "", 0)


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
    assert not text("ioreg-battery")  # Original recording used text mode, not -a.
    assert battery.read() == {"available": False, "reason": "no_battery"}
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


def test_local_xml_archive_preserves_native_types_and_capacity():
    import plistlib

    local = FIXTURE.parent / "macos-local"
    raw = (local / "ioreg-battery.plist").read_text()
    properties = plistlib.loads(raw.encode())[0]
    assert type(properties["ExternalConnected"]) is bool
    assert type(properties["Voltage"]) is int
    reading = battery.parse(raw, endpoint=True)
    assert reading["voltage_mv"] == properties["Voltage"]
    assert reading["remaining_mah"] == properties["BatteryData"]["RemainingCapacity"]
    assert (
        reading["telemetry"]["system_count"] == properties["PowerTelemetryData"]["SystemLoadAccumulatorCount"]
    )
    assert battery.parse((local / "ioreg-absent.plist").read_text()) == dict(
        available=False, reason="no_battery"
    )


def test_real_pmset_profiles_across_runner_and_local_versions(fake_runner):
    local = FIXTURE.parent / "macos-local"
    runner = pmset.parse_custom(text("pmset-custom"))
    laptop = pmset.parse_custom((local / "pmset-custom.stdout").read_text())
    assert set(runner) == {"a"} and set(laptop) == {"a", "b"}
    assert all(profile["sleep"].isdigit() for profile in laptop.values())
    assert pmset.parse_sleep_disabled((local / "pmset-current.stdout").read_text()) in ("0", "1")
    fake_runner.responses[("pmset", "-g", "cap")] = CmdResult((local / "pmset-cap.stdout").read_text(), "", 0)
    assert "sleep" in platform.supported_pmset_keys()
