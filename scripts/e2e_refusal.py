"""Source-only AppKit fixture; compilation happens only inside a real e2e run."""

import plistlib

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


def create(root, runner):
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
    result = runner(compile_command(root), timeout=120)
    if result.rc:
        raise RuntimeError(f"Refusing fixture compilation failed: {result}")
    result = runner(["codesign", "--force", "--sign", "-", str(app)], timeout=30)
    if result.rc:
        raise RuntimeError(f"Refusing fixture signing failed: {result}")
