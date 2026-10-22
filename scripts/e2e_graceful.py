"""Fixture-only AppleEvent handshakes, separate from product quit deadlines."""

from pathlib import Path

EVENT_SECONDS = 30
EXIT_SECONDS = 30
START_SECONDS = 30


def receipts(app):
    app = Path(app)
    return tuple(app.parent / (app.stem + suffix) for suffix in (".ready", ".quit"))


def compile_command(app):
    ready, quit = receipts(app)

    def write(path, value):
        quoted = str(path).replace("\\", "\\\\").replace('"', '\\"')
        return f'''set receipt to open for access POSIX file "{quoted}" with write permission
set eof receipt to 0
write "{value}" to receipt
close access receipt'''

    source = (
        "on idle\n"
        + write(ready, "ready")
        + "\nreturn 1\nend idle\n"
        + "on quit\n"
        + write(quit, "quit")
        + "\ncontinue quit\nend quit"
    )
    return ["osacompile", "-s", "-o", str(app), "-e", source]


def quit_command(app):
    # Address this concrete fixture, not a previously registered bundle with
    # the same test identifier left in LaunchServices' cache by another phase.
    quoted = str(app).replace("\\", "\\\\").replace('"', '\\"')
    return [
        "osascript",
        "-e",
        f"with timeout of {EVENT_SECONDS} seconds",
        "-e",
        f'tell application "{quoted}" to quit',
        "-e",
        "end timeout",
    ]
