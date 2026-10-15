"""Isolated deterministic data for the hidden demo mode."""

from __future__ import annotations

import os

from travel_mode.system import apps, launchd


def _configure_demo() -> None:
    """Install deterministic fixture data for screenshots and UI smoke checks."""
    import tempfile
    from pathlib import Path

    from .state import set_state_path_override
    from .system.shell import CmdResult, set_runner

    fixture = Path(__file__).parent.parent.parent / "tests" / "fixtures"
    demo_root = Path(tempfile.mkdtemp(prefix="travel-mode-demo-"))
    demo_state = demo_root / "state.json"
    demo_config = demo_root / "config.toml"
    demo_config.write_text(
        '[network]\napps = ["Clash Party", "aTrust", "UURemote"]\n\n[options]\nlanguage = "auto"\n',
        encoding="utf-8",
    )
    os.environ["TRAVEL_MODE_STATE"] = str(demo_state)
    os.environ["TRAVEL_MODE_CONFIG"] = str(demo_config)
    set_state_path_override(demo_state)

    class DemoRunner:
        def __call__(self, command, timeout=None, interactive=False):
            cmd = tuple(command)
            if cmd == ("ioreg", "-rn", "AppleSmartBattery"):
                return CmdResult(
                    '"ExternalConnected" = No\n"IsCharging" = No\n"Voltage" = 12000\n'
                    '"InstantAmperage" = 18446744073709550616\n"AppleRawCurrentCapacity" = 4000\n'
                    '"AppleRawMaxCapacity" = 5000\n"TimeRemaining" = 240\n',
                    "",
                    0,
                )
            if cmd == ("pmset", "-g", "custom"):
                return CmdResult((fixture / "pmset-g-custom.txt").read_text(), "", 0)
            if cmd == ("pmset", "-g"):
                return CmdResult((fixture / "pmset-g.txt").read_text(), "", 0)
            if cmd == ("pmset", "-g", "batt"):
                return CmdResult("Now drawing from 'Battery Power'\n -InternalBattery-0\t87%;", "", 0)
            if cmd == ("pmset", "-g", "ps"):
                return CmdResult("Currently drawing from 'Battery Power'\n", "", 0)
            if cmd == ("pmset", "-g", "assertions"):
                return CmdResult("Assertion status system-wide:\n", "", 0)
            if cmd == ("pmset", "-g", "cap"):
                return CmdResult("sleep displaysleep hibernatemode lowpowermode powermode\n", "", 0)
            if cmd == ("launchctl", "list"):
                return CmdResult("PID\tStatus\tLabel\n", "", 0)
            if cmd == ("mdutil", "-s", "/"):
                return CmdResult("/:\tIndexing enabled.\n", "", 0)
            if cmd == ("ps", "-axo", "pid=,%cpu=,command="):
                return CmdResult(
                    "\n".join(
                        f"{pid} 0.5 /Demo/{name}.app/Contents/MacOS/{name}"
                        for pid, name in enumerate(("aTrust", "Clash Party", "UURemote"), 1001)
                    ),
                    "",
                    0,
                )
            if cmd and cmd[0] in {"ps", "pgrep", "top", "mdfind"}:
                return CmdResult("", "", 0)
            if cmd[:2] == ("networksetup", "-listallhardwareports"):
                return CmdResult("Hardware Port: Wi-Fi\nDevice: en0\n", "", 0)
            if cmd[:2] == ("networksetup", "-getairportpower"):
                return CmdResult("Wi-Fi Power (en0): On\n", "", 0)
            if cmd[:2] == ("blueutil", "-p") or cmd[:2] == ("brightness", "-l"):
                return CmdResult("1\n", "", 0)
            return CmdResult("", "", 0)

    set_runner(DemoRunner())


def _configure_demo_views() -> None:

    apps.APP_DIRS = []
    apps.app_path = lambda name: f"/Demo/{name}.app" if name in {"Clash Party", "aTrust", "UURemote"} else ""
    apps.scan_apps = lambda: {
        "running": [
            {
                "name": "Google Drive",
                "path": "/Demo/Google Drive.app",
                "cpu": "1.2",
                "defact": "quit",
                "category": "sync",
                "rule_id": "google-drive",
                "category_action": "quit",
            },
            {
                "name": "Docker",
                "path": "/Demo/Docker.app",
                "cpu": "0.8",
                "defact": "ask",
                "category": "vm",
                "rule_id": "docker",
                "category_action": "quit",
            },
            {"name": "Demo Editor", "path": "/Demo/Demo Editor.app", "cpu": "0.5", "defact": "ask"},
        ],
        "nobundle": [],
    }
    launchd.svc_scan = lambda config=None: {
        "unloadable": [
            {
                "category": "backup",
                "label": "com.demo.backup",
                "pid": "123",
                "cpu": "0.4",
                "plist_path": "/tmp/demo.plist",
            },
            {
                "category": "updater",
                "label": "com.google.keystone.agent",
                "pid": "-",
                "cpu": "",
                "plist_path": "/tmp/demo-updater.plist",
            },
        ],
        "kept": ["com.demo.keep"],
        "candidates": [
            {
                "label": "com.demo.candidate",
                "pid": "-",
                "cpu": "",
                "plist_path": "/tmp/candidate.plist",
            }
        ],
        "invalid": [
            {"plist_path": "/tmp/demo-broken.plist", "error": "missing Label"},
            {"plist_path": "/tmp/demo-xml.plist", "error": "invalid property list"},
        ],
        "unmatched": [{"item": "demo-backup"}],
        "time_machine": {"running": False, "available": True},
    }
