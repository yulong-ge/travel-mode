"""Isolated deterministic data for the hidden demo mode."""

from __future__ import annotations

import os

from travel_mode.system import apps, launchd


def _configure_demo() -> None:
    """Install deterministic fixture data for screenshots and UI smoke checks."""
    import tempfile
    from pathlib import Path

    from .state import set_state_path_override
    from .system.health import PROCESS_COMMAND, native_command
    from .system.shell import CmdResult, set_runner

    fixture = Path(__file__).parent.parent.parent / "tests" / "fixtures"
    demo_root = Path(tempfile.mkdtemp(prefix="travel-mode-demo-", dir="/tmp"))
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
            if list(command) == native_command("displays"):
                return CmdResult('[{"id": 99, "builtin": false}]', "", 0)
            if list(command) == native_command("brightness"):
                return CmdResult("0.85", "", 0)
            if list(command) == PROCESS_COMMAND:
                return CmdResult(
                    f"900000 1 {os.getuid()} S 1 Thu Oct 15 12:00:00 2026 "
                    "/Demo/Editor.app/Contents/MacOS/Editor\n"
                    f"900001 1 {os.getuid()} S 35 Thu Oct 15 12:00:00 2026 "
                    "/Demo/Docker.app/Contents/MacOS/Docker",
                    "",
                    0,
                )
            if cmd[:3] == ("sudo", "-n", "-l"):
                return CmdResult("NOPASSWD: ALL", "", 0)
            if cmd == ("tmutil", "status"):
                return CmdResult("Backup session status:\n{ Running = 0; }", "", 0)
            if cmd == ("lsappinfo", "front"):
                return CmdResult("ASN:0x0-0x1:", "", 0)
            if cmd[:2] == ("lsappinfo", "info"):
                return CmdResult('pid = 900000\nbundleID="org.demo.editor"', "", 0)
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


def setup_rows():
    """A realistic-sized setup snapshot, grouped like the finite discovery result."""
    from . import app_policy
    from .categories import identify

    names = (
        "WeChat",
        "Slack",
        "Discord",
        "Signal",
        "Telegram",
        "Spotify",
        "Music",
        "Photos",
        "Preview",
        "Calendar",
        "Calculator",
        "Notes",
        "Reminders",
        "Books",
        "Mail",
        "Google Drive",
        "OneDrive",
        "Dropbox",
        "Box",
        "Nextcloud",
        "UURemote",
        "Amphetamine",
        "KeepingYouAwake",
        "Google Chrome",
        "Safari",
        "Firefox",
        "Visual Studio Code",
        "Terminal",
        "TextEdit",
        "VLC",
        "IINA",
        "HandBrake",
        "Audacity",
        "Calibre",
        "Anki",
        "Zotero",
        "Maccy",
        "Rectangle",
        "KeePassXC",
    )
    # Public CFBundleIdentifier values: model the same Apple-app exclusions as live setup.
    apple_ids = {
        "Music": "com.apple.Music",
        "Photos": "com.apple.Photos",
        "Preview": "com.apple.Preview",
        "Calendar": "com.apple.iCal",
        "Calculator": "com.apple.calculator",
        "Notes": "com.apple.Notes",
        "Reminders": "com.apple.reminders",
        "Books": "com.apple.iBooksX",
        "Mail": "com.apple.mail",
        "Safari": "com.apple.Safari",
        "TextEdit": "com.apple.TextEdit",
    }
    rows = [
        dict(
            name=name,
            path=f"/Demo/{name}.app",
            bundle_id=apple_ids.get(name, ""),
            running=True,
            **app_policy.recommend(
                name,
                rule=identify(name=name),
                assertion=name in {"UURemote", "Amphetamine", "KeepingYouAwake"},
            ),
        )
        for name in names
    ]
    return app_policy.setup_candidates(
        sorted(rows, key=lambda row: (row["reason"], row["name"].casefold())), []
    )


def _configure_demo_views() -> None:
    from . import app_policy

    app_policy.discover = setup_rows

    apps.APP_DIRS = []
    apps.app_path = lambda name: f"/Demo/{name}.app" if name in {"Clash Party", "aTrust", "UURemote"} else ""

    def scan():
        from .config import load_config

        scenario = load_config().options.scenario
        data = [
            dict(
                name=row["name"],
                path=row["path"],
                cpu="0.5",
                defact=row[scenario if scenario in {"work", "away"} else "work"],
                reason=row["reason"],
            )
            for row in setup_rows()
        ]
        data.insert(
            2,
            dict(
                name="Quit Refusal Demo",
                path="/Demo/Quit Refusal Demo.app",
                cpu="0.4",
                defact="quit",
                reason="provider",
            ),
        )
        return dict(running=data, nobundle=["Unmatched Demo 1", "Unmatched Demo 2"])

    apps.scan_apps = scan
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


def _configure_health_sources():
    """Two finite synthetic observations establish a repeated launcher for screenshots."""
    from .sleep_readiness import from_report, store
    from .system.health import PROCESS_COMMAND, collect
    from .system.shell import CmdResult, get_runner, set_runner

    previous = get_runner()

    class Sources:
        child = 900100

        def __call__(self, command, timeout=None, interactive=False):
            if command == PROCESS_COMMAND:
                rows = [
                    (900000, 1, 0, "/System/Library/CoreServices/loginwindow"),
                    (self.child, 900101, 0, "/usr/bin/caffeinate"),
                    (900101, 1, 0, "/opt/demo/bin/claude"),
                    (900200, 1, 85, "/opt/demo/bin/render-worker"),
                    (900201, 1, 65, "/opt/demo/bin/sync-worker"),
                    (900202, 1, 55, "/opt/demo/bin/build-worker"),
                ]
                return CmdResult(
                    "\n".join(
                        f"{pid} {ppid} {os.getuid()} S {cpu} Thu Oct 15 12:00:00 2026 {exe}"
                        for pid, ppid, cpu, exe in rows
                    ),
                    "",
                    0,
                )
            if command == ["pmset", "-g"]:
                return CmdResult("SleepDisabled 0\nlowpowermode 1", "", 0)
            if command == ["pmset", "-g", "assertions"]:
                return CmdResult(
                    "Assertion status system-wide:\n"
                    f'pid {self.child}(caffeinate): 00:03:00 PreventUserIdleSystemSleep named: "task"',
                    "",
                    0,
                )
            if command == ["ps", "-ww", "-p", str(self.child), "-o", "command="]:
                return CmdResult("/usr/bin/caffeinate -i -t 300", "", 0)
            return previous(command, timeout, interactive)

    source = Sources()
    set_runner(source)
    store(from_report(collect(clock=lambda: 0)))
    source.child = 900105
