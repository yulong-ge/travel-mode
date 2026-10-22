"""Real LaunchServices renderings and fail-closed app mutations."""

import json
import plistlib
from pathlib import Path

import jsonschema
import pytest

from travel_mode import structured
from travel_mode.actions.apps import ForceQuitApp, FreezeApp, QuitApp, QuitDaemonApp
from travel_mode.config import DaemonApp
from travel_mode.health_view import presentation
from travel_mode.i18n import set_language
from travel_mode.system import apps, health
from travel_mode.system.lsappinfo import ForegroundError, parse_info
from travel_mode.system.shell import CmdResult

FIXTURES = Path(__file__).parents[1] / "fixtures/lsappinfo"


@pytest.mark.parametrize(
    "sample,pid,bundle", [("local", 41918, "com.electron.lark"), ("runner", 358, "com.apple.finder")]
)
def test_observed_foreground_formats(sample, pid, bundle, fake_runner):
    raw = (FIXTURES / f"{sample}.txt").read_text()
    parsed = parse_info(raw)
    assert (parsed.pid, parsed.bundle_id) == (pid, bundle)
    fake_runner.responses[("lsappinfo", "info")] = CmdResult(raw, "", 0)
    fake_runner.responses[tuple(health.PROCESS_COMMAND)] = CmdResult(
        f"{pid} 1 0 S 0 Thu Oct 15 12:00:00 2026 "
        "/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder",
        "",
        0,
    )
    row = next(row for row in health.collect()["checks"] if row["id"] == "high_cpu")
    assert row["status"] == "clear", row


@pytest.mark.parametrize(
    "raw",
    [
        '"pid"=oops\n"CFBundleIdentifier"="org.example.app"',
        "pid = 0",
        "pid = 42\npid = broken",
        'pid = 42\n"CFBundleIdentifier"=broken',
        '"PID"=42',
    ],
)
def test_unknown_foreground_format_retains_original(raw):
    with pytest.raises(ForegroundError) as failure:
        parse_info(raw)
    assert repr(raw) in str(failure.value)


def machine(tmp_path, fake_runner, *, policy="adaptive"):
    path = tmp_path / "Applications/Worker.app"
    (path / "Contents").mkdir(parents=True)
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "org.example.worker"}))
    (tmp_path / "config.toml").write_text(f'[apps]\npolicy="{policy}"\nquit=["Worker"]\n')
    fake_runner.responses[("ps", "-axo", "pid=,%cpu=,command=")] = CmdResult(
        f"810 5 {path}/Contents/MacOS/Worker", "", 0
    )
    fake_runner.responses[("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm=")] = CmdResult(
        f"810 1 S Thu Oct 15 12:00:00 2026 {path}/Contents/MacOS/Worker", "", 0
    )
    fake_runner.responses[tuple(health.PROCESS_COMMAND)] = CmdResult(
        f"810 1 0 S 75 Thu Oct 15 12:00:00 2026 {path}/Contents/MacOS/Worker", "", 0
    )
    fake_runner.responses[("lsappinfo", "info")] = CmdResult('"pid"=broken', "", 0)
    return str(path)


@pytest.mark.parametrize("policy", ["adaptive", "legacy"])
def test_foreground_failure_keeps_audit_and_blocks_app_defaults(tmp_path, fake_runner, policy):
    machine(tmp_path, fake_runner, policy=policy)
    set_language("en")
    data = structured.check_data()
    schema = Path(__file__).parents[2] / "docs/json-schema/check.schema.json"
    jsonschema.validate(data, json.loads(schema.read_text()))
    assert "audit_error" not in data
    assert {"apps", "services", "battery", "power"} <= data.keys()
    assert data["apps"] and all(row["default_action"] == "skip" for row in data["apps"])
    assert data["app_safety"]["status"] == "blocked"
    assert '"pid"=broken' in data["app_safety"]["error"]
    assert "until a recheck succeeds" in data["app_safety"]["message"]
    report = data["health"]
    assert len(report["checks"]) == 9
    row = next(row for row in report["checks"] if row["id"] == "high_cpu")
    assert row["status"] == "unknown" and row["error"]
    assert not any(a["kind"] in {"quit", "freeze"} for r in report["checks"] for a in r["actions"])
    display = next(r for r in presentation(report) if r["id"] == "high_cpu")
    assert "until a recheck succeeds" in display["summary"]


@pytest.mark.parametrize("kind", ["quit", "force", "freeze", "daemon"])
@pytest.mark.parametrize("policy", ["adaptive", "legacy"])
def test_unknown_foreground_prevents_every_app_mutation_and_recheck_protects_target(
    tmp_path, fake_runner, kind, policy
):
    path = machine(tmp_path, fake_runner, policy=policy)
    plist = tmp_path / "agent.plist"
    plist.write_bytes(plistlib.dumps({"Label": "org.example.worker"}))
    action = {
        "quit": QuitApp(path, "Worker"),
        "force": ForceQuitApp(path),
        "freeze": FreezeApp(path),
        "daemon": QuitDaemonApp(DaemonApp("Worker", user_agents=(str(plist),)), {}),
    }[kind]
    with pytest.raises(ForegroundError):
        action.apply(None)
    assert not any(c[0] in {"kill", "osascript", "launchctl"} for c in fake_runner.calls)
    # Even a previously built plan must recheck, and cannot stop the now-foreground app.
    fake_runner.responses[("lsappinfo", "info")] = CmdResult(
        '"pid"=810\n"CFBundleIdentifier"="org.example.worker"', "", 0
    )
    with pytest.raises(PermissionError):
        action.apply(None)
    # Bundle identity protects helpers even when the foreground PID is different.
    fake_runner.responses[("lsappinfo", "info")] = CmdResult(
        '"pid"=811\n"CFBundleIdentifier"="org.example.worker"', "", 0
    )
    with pytest.raises(PermissionError):
        action.apply(None)
    assert not any(c[0] in {"kill", "osascript", "launchctl"} for c in fake_runner.calls)
    fake_runner.responses[("lsappinfo", "info")] = CmdResult(
        '"pid"=358\n"CFBundleIdentifier"="com.apple.finder"', "", 0
    )
    assert all(row["defact"] == "quit" for row in apps.scan_apps()["running"])
