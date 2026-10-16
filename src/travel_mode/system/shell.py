"""可注入的外部命令执行器。

业务层只通过本模块执行命令。测试和 dry-run 可以替换全局 runner，真实
subprocess 调用因此集中在一个位置。
"""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..i18n import t
from ..privileges import PRIVILEGED_PATHS

# Apple tools are selected independently of PATH, including sudo's target.
# Keep logical argv upstream for Runner doubles, journaling and dry-run output.
SYSTEM_TOOLS = {
    **PRIVILEGED_PATHS,
    "ioreg": "/usr/sbin/ioreg",
    "ps": "/bin/ps",
    "pgrep": "/usr/bin/pgrep",
    "pkill": "/usr/bin/pkill",
    "top": "/usr/bin/top",
    "mdfind": "/usr/bin/mdfind",
    "tmutil": "/usr/bin/tmutil",
    "osascript": "/usr/bin/osascript",
    "open": "/usr/bin/open",
    "sudo": "/usr/bin/sudo",
    "visudo": "/usr/sbin/visudo",
    "install": "/usr/bin/install",
    "rm": "/bin/rm",
    "cat": "/bin/cat",
    "system_profiler": "/usr/sbin/system_profiler",
    "plutil": "/usr/bin/plutil",
    "sw_vers": "/usr/bin/sw_vers",
    "lsappinfo": "/usr/bin/lsappinfo",
    "osacompile": "/usr/bin/osacompile",
    "codesign": "/usr/bin/codesign",
    "true": "/usr/bin/true",
    "sh": "/bin/sh",
    "sleep": "/bin/sleep",
    "caffeinate": "/usr/bin/caffeinate",
}
THIRD_PARTY_TOOLS = ("blueutil", "brightness")


class CommandResolutionError(FileNotFoundError):
    """A required executable cannot be selected safely."""


def _is_executable(path: str) -> bool:
    return Path(path).is_file() and os.access(path, os.X_OK)


def command_path(name: str) -> str | None:
    """Report a trusted system path or a PATH-selected optional executable."""
    fixed = SYSTEM_TOOLS.get(Path(name).name)
    if fixed is not None:
        return fixed if _is_executable(fixed) else None
    return shutil.which(name)


def optional_tool_paths() -> dict[str, str | None]:
    return {name: command_path(name) for name in THIRD_PARTY_TOOLS}


def _resolve_executable(name: str) -> str:
    path = command_path(name)
    if path is None:
        fixed = SYSTEM_TOOLS.get(Path(name).name)
        if fixed is not None:
            raise CommandResolutionError(t("shell.system_tool_missing", tool=name, path=fixed))
        raise CommandResolutionError(t("shell.executable_missing", tool=name))
    return str(Path(path).absolute())


def resolve_command(cmd: list[str]) -> list[str]:
    """Resolve executable positions, never arguments, without mutating logical argv."""
    if not cmd:
        raise ValueError(t("shell.empty_command"))
    resolved = [_resolve_executable(cmd[0]), *cmd[1:]]
    if Path(cmd[0]).name == "sudo":
        index = 1
        while index < len(cmd) and cmd[index].startswith("-"):
            option = cmd[index]
            index += 1
            if option == "--":
                break
            if option not in {"-n", "-l", "-v", "-k"}:
                raise ValueError(t("shell.sudo_option", option=option))
        if index < len(cmd):
            resolved[index:] = resolve_command(cmd[index:])
    return resolved


@dataclass(frozen=True)
class CmdResult:
    stdout: str
    stderr: str
    rc: int


class Runner(Protocol):
    def __call__(
        self, cmd: list[str], timeout: float | None = None, interactive: bool = False
    ) -> CmdResult: ...


def subprocess_runner(cmd: list[str], timeout: float | None = None, interactive: bool = False) -> CmdResult:
    """执行命令并保留稳定的错误码语义。"""
    try:
        executed = resolve_command(cmd)
        if interactive:
            proc = subprocess.run(executed, timeout=timeout, check=False)
            return CmdResult("", "", proc.returncode)
        proc = subprocess.run(
            executed, capture_output=True, text=True, errors="replace", timeout=timeout, check=False
        )
        return CmdResult(proc.stdout, proc.stderr, proc.returncode)
    except CommandResolutionError as exc:
        return CmdResult("", str(exc), 127)
    except FileNotFoundError:
        command = cmd[0] if cmd else "<empty command>"
        return CmdResult("", f"command not found: {command}", 127)
    except subprocess.TimeoutExpired:
        return CmdResult("", "timeout", 124)


_runner: Runner = subprocess_runner
_dry_run = False
_dry_run_commands: list[list[str]] = []
_dry_run_unknowns: list[str] = []


class SudoReadError(RuntimeError):
    """A privileged observation failed without prompting for credentials."""


class ReadError(RuntimeError):
    """An expected read-only command or its required output failed."""


def dry_run_unknowns() -> tuple[str, ...]:
    return tuple(_dry_run_unknowns)


def record_unknown(error: SudoReadError) -> None:
    _dry_run_unknowns.append(str(error))
    print(t("sudo.unknown", detail=str(error)), file=sys.stderr)


_dry_run_state_dir: Path | None = None


def _dry_run_readonly(cmd: list[str]) -> bool:
    if not cmd:
        return False
    base, args = cmd[0], cmd[1:]
    if base == sys.executable:
        return (
            len(args) == 3
            and args[:2] == ["-m", "travel_mode.system.health_native"]
            and args[2] in {"displays", "brightness"}
        )
    if base == "tmutil":
        return args == ["status"]
    if base == "lsappinfo":
        import re

        return args == ["front"] or (
            len(args) == 4
            and args[:3] == ["info", "-only", "pid,bundleid"]
            and bool(re.fullmatch(r"ASN:0x[0-9a-fA-F]+-0x[0-9a-fA-F]+:", args[3]))
        )
    if base == "sudo":
        return (
            args[:2] == ["-n", "-l"]
            or args == ["-n", "true"]
            or (args[:1] == ["-n"] and _dry_run_readonly(args[1:]))
        )
    if base == "pmset":
        return len(args) >= 1 and args[0] == "-g"
    if base == "launchctl":
        return args == ["list"] or (len(args) == 2 and args[0] == "print")
    if base == "ioreg":
        return args == ["-rn", "AppleSmartBattery"]
    if base == "mdutil":
        return args[:1] == ["-s"]
    if base in {"ps", "pgrep", "top", "mdfind"}:
        return True
    if base == "networksetup":
        return bool(args) and (args[0].startswith("-list") or args[0].startswith("-get"))
    if base == "blueutil":
        return not args or args == ["-p"]
    if base == "brightness":
        return args == ["-l"]
    return False


class DryRunRunner:
    """透传只读探测，并记录所有修改命令而不执行。"""

    def __init__(self, real: Runner):
        self.real = real

    def __call__(self, cmd: list[str], timeout: float | None = None, interactive: bool = False) -> CmdResult:
        if cmd == ["sudo", "-v"] and not (interactive and sys.stdin.isatty()):
            return CmdResult("", t("sudo.no_tty"), 1)
        if _dry_run_readonly(cmd) or (cmd == ["sudo", "-v"] and interactive and sys.stdin.isatty()):
            return self.real(cmd, timeout, interactive)
        bare = cmd[1:] if cmd[:1] == ["sudo"] else cmd
        if bare[:2] == ["launchctl", "unload"] and cmd in _dry_run_commands:
            print(t("dry_run.already_planned", command=shlex.join(cmd)))
            return CmdResult("", "", 0)
        _dry_run_commands.append(list(cmd))
        print(f"[dry-run] {shlex.join(cmd)}")
        return CmdResult("", "", 0)


class ReadOnlyRunner:
    """Allow only host observations; reject mutations and every sudo command."""

    readonly = True

    def __call__(self, cmd: list[str], timeout: float | None = None, interactive: bool = False) -> CmdResult:
        allowed = bool(cmd) and cmd[0] != "sudo" and (_dry_run_readonly(cmd) or cmd == ["tmutil", "status"])
        if not allowed or interactive:
            raise PermissionError(f"Read-only TUI refused command: {shlex.join(cmd)}")
        return subprocess_runner(cmd, timeout, interactive)


def enable_dry_run(real: Runner | None = None) -> Path:
    global _runner, _dry_run, _dry_run_state_dir
    if _dry_run:
        return _dry_run_state_dir  # type: ignore[return-value]
    _dry_run = True
    _dry_run_commands.clear()
    _dry_run_unknowns.clear()
    _dry_run_state_dir = Path(tempfile.mkdtemp(prefix="travel-mode-dry-run-"))
    _runner = DryRunRunner(real or _runner)
    return _dry_run_state_dir


def disable_dry_run() -> None:
    global _runner, _dry_run, _dry_run_state_dir
    if isinstance(_runner, DryRunRunner):
        _runner = _runner.real
    _dry_run = False
    _dry_run_state_dir = None


def is_dry_run() -> bool:
    return _dry_run


def dry_run_commands() -> tuple[list[str], ...]:
    return tuple(_dry_run_commands)


def dry_run_state_dir() -> Path | None:
    return _dry_run_state_dir


def set_runner(r: Runner) -> None:
    global _runner
    _runner = r


def get_runner() -> Runner:
    return _runner


def _invoke(cmd: list[str], timeout: float | None, interactive: bool) -> CmdResult:
    from ..actions.journal import complete_command, prepare_command

    privileged_read = cmd[:1] == ["sudo"] and _dry_run_readonly(cmd[1:])
    if privileged_read:
        cmd = ["sudo", "-n", *cmd[1:]]
    elif cmd[:2] == ["sudo", "-n"]:
        privileged_read = _dry_run_readonly(cmd[2:])
    if cmd[:1] == ["sudo"] and (privileged_read or cmd[1:2] not in (["-v"], ["-n"])):
        from .sudo import ensure_command

        if not is_dry_run() or (privileged_read and sys.stdin.isatty()):
            ensure_command(cmd[2:] if cmd[1:2] == ["-n"] else cmd[1:])
    prepared = prepare_command(cmd)
    result = _runner(cmd, timeout, True) if interactive else _runner(cmd, timeout)
    if privileged_read and result.rc:
        raise SudoReadError(
            t(
                "sudo.read_failed",
                command=shlex.join(cmd),
                rc=result.rc,
                detail=result.stderr or result.stdout,
            )
        )
    if prepared:
        complete_command(cmd, result.rc)
    return result


def run_out(args, timeout=None, sudo=False, interactive=False):
    cmd = (["sudo"] if sudo else []) + list(args)
    result = _invoke(cmd, timeout, interactive)
    return result.stdout, result.rc


def run_rc(args, timeout=None, sudo=False, interactive=False):
    cmd = (["sudo"] if sudo else []) + list(args)
    result = _invoke(cmd, timeout, interactive)
    if result.rc == 127:
        print(t("shell.command_not_installed", v0=cmd[0]), file=sys.stderr)
    return result.stdout + result.stderr, result.rc


def have_sudo():
    return run_out(["-n", "true"], sudo=True)[1] == 0


def pgrep_lines(pattern):
    out, rc = run_rc(["pgrep", "-fl", pattern], timeout=10)
    if rc not in (0, 1):
        raise RuntimeError(t("json.command_failed", command=f"pgrep -fl {pattern}", rc=rc, detail=out))
    return [line for line in out.splitlines() if line.strip()]


def pgrep_alive(pattern):
    return bool(pgrep_lines(pattern))


def first_line(out):
    return out.splitlines()[0] if out else ""


def _read(args, timeout=20, sudo=False):
    output, rc = run_rc(args, timeout=timeout, sudo=sudo)
    if rc:
        raise ReadError(t("json.command_failed", command=shlex.join(args), rc=rc, detail=output))
    return output
