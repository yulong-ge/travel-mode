"""Isolated deterministic data for the hidden demo mode."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from travel_mode.system import apps, launchd


def _configure_demo(directory=None) -> None:
    """Install deterministic fixture data for screenshots and UI smoke checks."""
    from .paths import state_path
    from .state import set_state_path_override
    from .system.health import PROCESS_COMMAND, native_command
    from .system.shell import CmdResult, set_runner

    fixture = Path(__file__).parent.parent.parent / "tests" / "fixtures"
    # CLI owns the demo lifecycle root; do not allocate a second, orphaned tree.
    demo_root = Path(directory) if directory is not None else state_path().parent
    demo_state = demo_root / "state.json"
    demo_config = demo_root / "config.toml"
    demo_config.write_text(
        '[apps]\npolicy="adaptive"\n[network]\n'
        'apps = ["Tunnelblick", "WireGuard", "AnyDesk"]\n\n[options]\nlanguage = "auto"\n',
        encoding="utf-8",
    )
    os.environ["TRAVEL_MODE_STATE"] = str(demo_state)
    os.environ["TRAVEL_MODE_CONFIG"] = str(demo_config)
    set_state_path_override(demo_state)

    class DemoRunner:
        def __call__(self, command, timeout=None, interactive=False):
            cmd = tuple(command)
            if cmd == ("sysctl", "-n", "kern.sleeptime", "kern.waketime"):
                return CmdResult("{ sec = 0, usec = 0 }\n{ sec = 0, usec = 0 }\n", "", 0)
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
                        for pid, name in enumerate(("WireGuard", "Tunnelblick", "AnyDesk"), 1001)
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


@contextmanager
def environment(parent=None):
    """Debug/script entry points own their fixtures even when startup or the UI fails."""
    import tempfile

    from . import state
    from .system import shell

    variables = ("TRAVEL_MODE_CONFIG", "TRAVEL_MODE_STATE")
    previous = {key: os.environ.get(key) for key in variables}
    override, runner = state._STATE_PATH_OVERRIDE, shell.get_runner()
    with tempfile.TemporaryDirectory(prefix="travel-mode-demo-", dir=parent) as directory:
        try:
            _configure_demo(directory)
            yield Path(directory)
        finally:
            shell.set_runner(runner)
            state.set_state_path_override(override)
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def setup_rows():
    """A realistic-sized setup snapshot, grouped like the finite discovery result."""
    from . import app_policy
    from .categories import identify

    names = (
        "zoom.us",
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
        "AnyDesk",
        "AmphetamineNet",
        "KeepingYouAwake",
        "Google Chrome",
        "Safari",
        "Firefox",
        "Visual Studio Code",
        "Terminal",
        "TextEdit",
        "VLC",
        "Video Studio",
        "Archive Manager",
        "Audio Studio",
        "Book Library",
        "Study Cards",
        "Research Reader",
        "Clipboard Shelf",
        "Window Desk",
        "Password Vault",
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
                assertion=name in {"AnyDesk", "AmphetamineNet", "KeepingYouAwake"},
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
    from . import onboarding

    def setup_snapshot(**kwargs):
        data = setup_rows()
        from .categories import identify

        for row in data:
            rule = identify(name=row["name"])
            row.update(group=onboarding.group(rule["category"] if rule else None), configured=False)
        services = [
            dict(
                label="org.example." + name, name=name, group=group, recommendation="unload", configured=False
            )
            for name, group in (
                ("sync", "sync-backup"),
                ("backup", "sync-backup"),
                ("updater", "updater"),
                ("second-updater", "updater"),
            )
        ]
        from .sleep_readiness import evaluate
        from .system.app_snapshot import Process

        return dict(
            apps=data,
            services=services,
            readiness=evaluate(
                "1",
                processes=[Process("900001", "0", "/Demo/AmphetamineNet.app/Contents/MacOS/AmphetamineNet")],
            ),
        )

    onboarding.scan = setup_snapshot

    apps.APP_DIRS = []
    apps.app_path = lambda name: (
        f"/Demo/{name}.app" if name in {"Tunnelblick", "WireGuard", "AnyDesk"} else ""
    )

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
                "label": "org.example.updater",
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
                    (900101, 1, 0, "/opt/demo/bin/build-worker"),
                    (900200, 1, 85, "/opt/demo/bin/render-worker"),
                    (900201, 1, 65, "/opt/demo/bin/sync-worker"),
                    (900202, 1, 55, "/opt/demo/bin/build-worker"),
                    (
                        900203,
                        1,
                        25,
                        "/System/Library/Frameworks/CoreServices.framework/Frameworks/Metadata.framework/Support/mds",
                    ),
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
