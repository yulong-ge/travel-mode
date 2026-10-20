"""Source-only AppKit fixture; compilation happens only inside a real e2e run."""

import plistlib

from harness_evidence import write_json

COMPILE_TIMEOUT = 120

IDENTIFIER = "com.example.travelmode-e2e.refusing"
SOURCE = """import AppKit
final class RefusingDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        return .terminateCancel
    }
}
let app = NSApplication.shared
let delegate = RefusingDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
"""
QUIT_EVENT = [
    "osascript",
    "-e",
    "with timeout of 8 seconds",
    "-e",
    f'tell application id "{IDENTIFIER}" to quit',
    "-e",
    "end timeout",
]


def compile_command(root):
    return [
        "swiftc",
        "-module-cache-path",
        str(root / "swift-cache"),
        str(root / "Refusing.swift"),
        "-o",
        str(root / "TravelModeE2E-Refusing.app/Contents/MacOS/refusing"),
    ]


def create(root, runner, *, evidence=None):
    app = runner.refusing
    (app / "Contents/MacOS").mkdir(parents=True)
    (root / "Refusing.swift").write_text(SOURCE)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            dict(
                CFBundleIdentifier=IDENTIFIER,
                CFBundleExecutable="refusing",
                CFBundleName=app.stem,
                CFBundlePackageType="APPL",
                LSUIElement=True,
            )
        )
    )
    path = (evidence or root) / "refusing-build.json"
    stages = []
    for name, command, timeout in (
        ("compilation", compile_command(root), COMPILE_TIMEOUT),
        ("signing", ["codesign", "--force", "--sign", "-", str(app)], 30),
    ):
        row = dict(stage=name, command=command, timeout=timeout, status="running")
        stages.append(row)
        write_json(path, {"stages": stages})
        try:
            result = runner(command, timeout=timeout)
        except BaseException as error:
            row.update(status="interrupted", error=f"{type(error).__name__}: {error}")
            for key in ("stdout", "stderr"):
                output = root / ("refusing-build." + key)
                row[key] = output.read_text(errors="replace") if output.exists() else None
            write_json(path, {"stages": stages})
            raise
        row.update(status="complete", rc=result.rc, stdout=result.stdout, stderr=result.stderr)
        write_json(path, {"stages": stages})
        if result.rc:
            raise RuntimeError(f"Refusing fixture {name} failed: {result}")
