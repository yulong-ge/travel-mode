"""可注入的外部命令执行器。

业务层只通过本模块执行命令。测试和 dry-run 可以替换全局 runner，真实
subprocess 调用因此集中在一个位置。
"""

import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..i18n import t


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
        if interactive:
            proc = subprocess.run(cmd, timeout=timeout, check=False)
            return CmdResult("", "", proc.returncode)
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=timeout, check=False
        )
        return CmdResult(proc.stdout, proc.stderr, proc.returncode)
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
    if base == "sudo":
        return (
            args[:2] == ["-n", "-l"]
            or args == ["-n", "true"]
            or (args[:1] == ["-n"] and _dry_run_readonly(args[1:]))
        )
    if base == "pmset":
        return len(args) >= 1 and args[0] == "-g"
    if base == "launchctl":
        return args == ["list"]
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
        raise RuntimeError(t("json.command_failed", command=shlex.join(args), rc=rc, detail=output))
    return output
