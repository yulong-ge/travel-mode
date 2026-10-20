"""Deterministic macOS fixture. Every command goes through the strict FakeRunner."""

import json
import re
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

import travel_mode.flows.common as common
import travel_mode.snapshots as snapshots
from conftest import FakeRunner, _readonly_responses
from travel_mode import state
from travel_mode.i18n import get_language, set_language
from travel_mode.system import apps as system_apps
from travel_mode.system import daemon_discovery
from travel_mode.system.shell import CmdResult, set_runner

from .contracts import canonical_name, load_contract, validate_contract

GOLDEN_DIR = Path(__file__).parent / "golden"


@pytest.fixture
def serial_batches(monkeypatch):
    """Golden traces use a deterministic scheduler; fault/concurrency tests use real overlap."""
    from travel_mode.actions.executor import Executor

    monkeypatch.setattr(Executor, "batch_workers", 1)


class FixedTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 15, 12)


@pytest.fixture
def contract_root(request):
    """Keep the original contracts' path wrapping independent of pytest/host paths.

    Rich wraps before artifact normalization. The recorded contracts use an
    87-column parent plus pytest's (at most 30-character) test name and counter.
    Use that explicit display geometry without the author's TMPDIR or username.
    Each invocation owns a unique temporary directory and removes it afterwards.
    """
    with tempfile.TemporaryDirectory(prefix="tm-contract-", dir="/tmp") as temporary:
        parent = Path(temporary) / ("x" * (87 - len(temporary) - 1))
        assert len(str(parent)) == 87
        name = re.sub(r"[\W]", "_", request.node.name)[:30] + "0"
        root = parent / name
        root.mkdir(parents=True)
        yield root


@pytest.fixture
def machine(contract_root, tmp_paths, darwin_platform, monkeypatch):
    import os
    import plistlib

    monkeypatch.setattr("travel_mode.system.health.monotonic", lambda: 0.0)
    monkeypatch.setattr("travel_mode.sessions.utc_now", lambda: "2026-10-15T12:00:00+00:00")
    monkeypatch.setattr("travel_mode.sessions.uuid4", lambda: "fixture-session")

    root = contract_root
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("TRAVEL_MODE_CONFIG", str(root / "config.toml"))
    monkeypatch.setenv("TRAVEL_MODE_STATE", str(root / "state.json"))
    apps = root / "Applications"
    agents = root / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    names = ("Quit Demo", "Freeze Demo", "Skip Demo", "Ask Demo", "Remote Demo", "Network Demo")
    paths = {name: str(apps / f"{name}.app") for name in names}
    processes = {}
    for i, (name, path) in enumerate(paths.items(), 910001):
        contents = Path(path) / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": f"org.demo.app{i}"}))
        processes[str(i)] = ["S", f"{path}/Contents/MacOS/{name}"]
    plist = agents / "org.demo.worker.plist"
    plist.write_bytes(plistlib.dumps({"Label": "org.demo.worker"}))
    daemon = root / "org.demo.daemon.plist"
    daemon.write_bytes(plistlib.dumps({"Label": "org.demo.daemon"}))
    config = root / "config.toml"
    config.write_text(
        '[apps]\nquit=["Quit Demo"]\nfreeze=["Freeze Demo"]\nskip=["Skip Demo"]\n'
        'ask=["Ask Demo"]\ndefault="skip"\n'
        '[[apps.with_daemon]]\nname="Remote Demo"\ngroup="network"\n'
        f'system_daemons=["{daemon}"]\n'
        '[network]\napps=["Network Demo", "Remote Demo"]\n'
        '[services]\nunload=["org.demo.worker"]\n[options]\nlanguage="en"\n'
    )
    monkeypatch.setattr(system_apps, "APP_DIRS", [str(apps)])
    monkeypatch.setattr(common, "STATE_PATH", root / "state.json")
    monkeypatch.setattr(common, "CONF_PATH", config)
    monkeypatch.setattr(snapshots, "datetime", FixedTime)
    monkeypatch.setattr(state, "datetime", FixedTime)
    monkeypatch.setattr(daemon_discovery, "plist_directories", lambda: (agents,))
    monkeypatch.setattr(daemon_discovery, "app_directories", lambda: (apps,))
    monkeypatch.setattr("shutil.which", lambda name: f"/fixture/bin/{name}")
    monkeypatch.setattr("travel_mode.system.brightness.available", lambda: True)
    monkeypatch.setenv("COLUMNS", "120")
    monkeypatch.setenv("NO_COLOR", "1")
    responses = _readonly_responses()
    responses.update(
        {
            ("sudo", "pmset"): CmdResult("", "", 0),
            ("sudo", "launchctl"): CmdResult("", "", 0),
            ("sudo", "mdutil"): CmdResult("", "", 0),
            ("sudo", "networksetup"): CmdResult("", "", 0),
            ("osascript",): CmdResult("", "", 0),
            ("launchctl", "unload"): CmdResult("", "", 0),
            ("launchctl", "load"): CmdResult("", "", 0),
            ("kill",): CmdResult("", "", 0),
            ("pmset", "displaysleepnow"): CmdResult("", "", 0),
            ("brightness",): CmdResult("", "", 0),
            ("open",): CmdResult("", "", 0),
            ("fixture-editor",): CmdResult("", "", 0),
            ("pmset", "-g"): CmdResult("SleepDisabled 1\nlowpowermode 0\n", "", 0),
            ("lsappinfo", "info"): CmdResult('pid = 919997\nbundleID="com.apple.Terminal"', "", 0),
            ("pmset", "-g", "cap"): CmdResult(
                "sleep displaysleep lowpowermode powernap tcpkeepalive womp standby hibernatemode "
                "ttyskeepawake disksleep lessbright networkoversleep",
                "",
                0,
            ),
            ("pmset", "-g", "assertions"): CmdResult(
                "Assertion status system-wide:\n"
                'pid 919999(caffeinate): PreventUserIdleDisplaySleep named: "caffeinate -d"\n'
                'pid 910005(Remote Demo): PreventSystemSleep named: "Remote Demo"\n'
                'pid 910001(Quit Demo): PreventUserIdleDisplaySleep named: "audio"\n',
                "",
                0,
            ),
            ("pgrep", "-fl", "caffeinate"): CmdResult("919999 caffeinate -d\n", "", 0),
            ("pmset", "-g", "log"): CmdResult(
                "2026-10-14 21:00:00 Entering Sleep Using Batt Charge: 90\n"
                "2026-10-15 07:00:00 Maintenance Sleep Using Batt Charge: 85\n"
                "2026-10-15 07:01:00 Assertions Summary Using Batt Charge: 85\n"
                "2026-10-15 01:00:00 DarkWake BATT due to RTC/Maintenance\n",
                "",
                0,
            ),
        }
    )
    processes["919999"] = ["S", "/usr/bin/caffeinate"]
    processes["919998"] = ["S", "/usr/bin/caffeinate"]  # User's process: no assertion.
    processes["919997"] = ["S", "/Applications/Terminal.app/Contents/MacOS/Terminal"]
    fake = FakeRunner(responses)
    loaded = {"org.demo.worker", "org.demo.daemon"}

    def runner(command, timeout=None, interactive=False):
        cmd = tuple(command)
        bare = cmd[1:] if cmd[:1] == ("sudo",) else cmd
        if bare[:1] == ("-n",):
            bare = bare[1:]
        if bare == ("ps", "-axo", "pid=,ppid=,uid=,stat=,%cpu=,lstart=,comm="):
            responses[cmd] = CmdResult(
                "\n".join(
                    f"{pid} {919997 if pid == '919999' else 1} {os.getuid()} {stat} 1.0 "
                    f"Thu Oct 15 12:00:00 2026 {path}"
                    for pid, (stat, path) in processes.items()
                ),
                "",
                0,
            )
        elif bare[:3] == ("ps", "-ww", "-p"):
            pid = bare[3]
            responses[cmd] = CmdResult(processes[pid][1] + (" -d" if pid == "919999" else ""), "", 0)
        elif bare == ("ps", "-axo", "pid=,ppid=,stat=,lstart=,comm="):
            responses[cmd] = CmdResult(
                "\n".join(
                    f"{pid} {919997 if pid == '919999' else 1} {stat} Thu Oct 15 12:00:00 2026 {path}"
                    for pid, (stat, path) in processes.items()
                ),
                "",
                0,
            )
        elif bare == ("ps", "-ww", "-axo", "pid=,command="):
            responses[cmd] = CmdResult(
                "\n".join(
                    f"{pid} {path}" + (" -d" if pid == "919999" else "")
                    for pid, (_, path) in processes.items()
                ),
                "",
                0,
            )
        elif bare == ("ps", "-axo", "pid=,%cpu=,command="):
            responses[cmd] = CmdResult(
                "\n".join(f"{pid} 1.0 {path}" for pid, (_, path) in processes.items()), "", 0
            )
        elif bare == ("ps", "-axo", "pid,stat,comm"):
            responses[cmd] = CmdResult(
                "\n".join(f"{pid} {stat} {path}" for pid, (stat, path) in processes.items()), "", 0
            )
        elif bare == ("launchctl", "list"):
            responses[cmd] = CmdResult("\n".join(f"- 0 {label}" for label in sorted(loaded)), "", 0)
        result = fake(command, timeout, interactive)
        if result.rc:
            return result
        if bare[:2] == ("launchctl", "unload"):
            with Path(bare[2]).open("rb") as handle:
                loaded.discard(plistlib.load(handle)["Label"])
        elif bare[:1] == ("osascript",):
            target = bare[4]
            for pid in list(processes):
                if f"org.demo.app{pid}" in target:
                    del processes[pid]
        elif bare[:1] == ("kill",):
            if bare[1] == "-TERM":
                del processes[bare[2]]
            else:
                processes[bare[2]][0] = "T" if bare[1] == "-STOP" else "S"
        return result

    set_runner(runner)
    previous = get_language()
    set_language("en")
    yield dict(
        root=root,
        paths=paths,
        fake=fake,
        processes=processes,
        config=config,
        daemon=daemon,
        plist=plist,
        loaded=loaded,
    )
    set_language(previous)


def normalize(value, root):
    import sys

    if isinstance(value, dict):
        return {key: normalize(item, root) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [normalize(item, root) for item in value]
    if isinstance(value, str):
        if value == sys.executable:
            return "<PYTHON>"
        value = value.replace(str(root), "<ROOT>")
        return re.sub(r"2026-10-15 12:00:00", "<TIME>", value)
    return value


def assert_golden(name, value, root):
    """Record missing, untracked contracts only when explicitly requested."""
    import os
    import subprocess

    target = GOLDEN_DIR / f"{canonical_name(name)}.json"
    value = validate_contract(normalize(value, root), target)
    actual = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if os.environ.get("TRAVEL_MODE_RECORD_INITIAL") == "1" and not target.exists():
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(target)], capture_output=True, check=False
        )
        if tracked.returncode == 0:
            raise RuntimeError(f"Golden is tracked; initial recording cannot recreate it: {target}")
        target.parent.mkdir(exist_ok=True)
        target.write_text(actual)
    if not target.exists():
        diagnostic = Path("/tmp/tm-verify/golden-actual")
        diagnostic.mkdir(parents=True, exist_ok=True)
        (diagnostic / f"{name}.json").write_text(actual)
        raise FileNotFoundError(f"Missing characterization golden: {target}; actual saved to {diagnostic}")
    expected = load_contract(target)
    if expected != value:
        # Diagnostic artifact only; never update an expected golden here.
        diagnostic = Path("/tmp/tm-verify/golden-actual")
        diagnostic.mkdir(parents=True, exist_ok=True)
        (diagnostic / f"{name}.json").write_text(actual)
    assert expected == value


def artifact(machine, rc):
    root = machine["root"]
    path = root / "state.json"
    return {
        "commands": machine["fake"].calls,
        "state": json.loads(path.read_text()) if path.exists() else None,
        "exit_code": rc,
    }
