"""Policy generation and administrator Actions never invoke a host process."""

import ast
import fnmatch
import sys
from pathlib import Path

import pytest

from travel_mode import cli, sudoers
from travel_mode.actions.base import Context
from travel_mode.actions.executor import Executor
from travel_mode.actions.network import WifiOff
from travel_mode.actions.power import EnableSleep, RestoreDefaults, SetPowerProfile, StopSpotlight
from travel_mode.actions.sudoers import InstallSudoers, UninstallSudoers
from travel_mode.privileges import PRIVILEGED_PATHS, PRIVILEGES, rules
from travel_mode.system import shell
from travel_mode.system.shell import CmdResult


@pytest.fixture
def policy(tmp_path, monkeypatch, fake_runner):
    target = tmp_path / "travel-mode"
    monkeypatch.setattr(sudoers, "TARGET", target)
    monkeypatch.setattr(sudoers.pwd, "getpwuid", lambda _: type("Account", (), {"pw_name": "example_user"})())
    for stream in (sys.stdin, sys.stdout):
        monkeypatch.setattr(stream, "isatty", lambda: True)
    fake_runner.responses.update(
        {
            ("visudo", "-c", "-f"): CmdResult("parsed OK", "", 0),
            ("sudo", "install"): CmdResult("", "", 0),
            ("sudo", "rm"): CmdResult("", "", 0),
        }
    )
    return target


@pytest.mark.parametrize("scope", ["pmset", "all"])
def test_snippet_comes_from_operational_catalog_and_fixed_paths(scope):
    text = sudoers.snippet(scope, "example_user")
    entries = [line for line in text.splitlines() if not line.startswith("#")]
    assert entries == [
        f'"example_user" ALL=(root) NOPASSWD: {rule.path}'
        + (" " + rule.arguments if rule.arguments is not None else "")
        for rule in rules(scope)
    ]
    assert all(shell.SYSTEM_TOOLS[rule.tool] == rule.path for rule in PRIVILEGES)
    assert all(Path(rule.path).is_absolute() for rule in PRIVILEGES)
    assert "/etc/sudoers.d/travel-mode-test" not in text
    assert not any(tool in text for tool in ("/usr/bin/install", "/bin/rm", "/bin/cat", "/usr/sbin/visudo"))


def test_operational_catalog_covers_actual_sudo_command_sites():
    tools = set()
    for path in Path("src/travel_mode").rglob("*.py"):
        if path.name in {"sudoers.py", "sudo.py", "shell.py"}:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                isinstance(item, ast.Assign)
                and isinstance(item.value, ast.Constant)
                and item.value.value is True
                and any(isinstance(n, ast.Name) and n.id == "sudo" for n in item.targets)
                for item in node.body
            ):
                for statement in ast.walk(node):
                    if isinstance(statement, ast.Return) and isinstance(statement.value, ast.List):
                        first = statement.value.elts[0]
                        if isinstance(first, ast.Constant) and not first.value.startswith("-"):
                            tools.add(first.value)
            if isinstance(node, ast.Call) and any(k.arg == "sudo" for k in node.keywords):
                for argument in node.args:
                    if (
                        isinstance(argument, ast.List)
                        and argument.elts
                        and isinstance(argument.elts[0], ast.Constant)
                    ):
                        tools.add(argument.elts[0].value)
    assert tools == set(PRIVILEGED_PATHS)
    for action in (
        EnableSleep(),
        SetPowerProfile(["lowpowermode", "1"]),
        StopSpotlight(),
        WifiOff("en0"),
        RestoreDefaults(),
    ):
        cmd = action.command()
        assert any(
            rule.tool == cmd[0]
            and (rule.arguments is None or fnmatch.fnmatchcase(" ".join(cmd[1:]), rule.arguments))
            for rule in PRIVILEGES
        )


@pytest.mark.parametrize(
    "name", ["ALL", "all", "a b", "a\nb", "user:ALL", "u,other", "u\\x", "u%", "é", "-user", "1user"]
)
def test_usernames_cannot_inject_sudoers_syntax_or_aliases(name):
    with pytest.raises(sudoers.SudoersError):
        sudoers.snippet(user=name)


def test_uppercase_username_is_quoted_as_a_literal():
    assert '"EXAMPLE_USER" ALL=' in sudoers.snippet(user="EXAMPLE_USER")


def test_install_validates_displays_then_confirms_before_exact_install(
    policy, monkeypatch, fake_runner, capsys
):
    for stream in (sys.stdin, sys.stdout):
        monkeypatch.setattr(stream, "isatty", lambda: True)

    def confirm(prompt):
        assert fake_runner.calls[-1][:3] == ("visudo", "-c", "-f")
        assert str(policy) in capsys.readouterr().out
        return "yes"

    monkeypatch.setattr("builtins.input", confirm)
    assert Executor(Context(None)).execute(InstallSudoers())
    cmd = fake_runner.calls[-1]
    assert cmd[:9] == ("sudo", "install", "-m", "0440", "-o", "root", "-g", "wheel", cmd[8])
    assert cmd[-1] == str(policy)
    assert not any("travel-mode-test" in part for c in fake_runner.calls for part in c)


@pytest.mark.parametrize(
    "force,answers,success", [(False, [], False), (True, ["yes", "no"], False), (True, ["yes", "yes"], True)]
)
def test_existing_policy_requires_force_and_second_confirmation(
    policy, monkeypatch, fake_runner, force, answers, success
):
    policy.write_text("existing policy")
    sibling = policy.with_name("travel-mode-test")
    sibling.write_text("maintainer rule")
    responses = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    if success:
        assert Executor(Context(None)).execute(InstallSudoers(force=force))
    else:
        with pytest.raises(sudoers.SudoersError):
            Executor(Context(None)).execute(InstallSudoers(force=force))
        assert not any(c[:2] == ("sudo", "install") for c in fake_runner.calls)
    assert sibling.read_text() == "maintainer rule"


def test_invalid_visudo_output_fails_before_confirmation_or_install(policy, fake_runner, monkeypatch):
    fake_runner.responses[("visudo", "-c", "-f")] = CmdResult("", "syntax error", 1)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("confirmation before validation"))
    with pytest.raises(sudoers.SudoersError, match="syntax error"):
        Executor(Context(None)).execute(InstallSudoers())
    assert not any(c[0] == "sudo" for c in fake_runner.calls)


def test_noninteractive_install_refused_before_any_command(policy, fake_runner, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(sudoers.SudoersError):
        Executor(Context(None)).execute(InstallSudoers())
    assert not fake_runner.calls


def test_uninstall_only_removes_fixed_policy_after_confirmation(policy, monkeypatch, fake_runner):
    policy.write_text("example")
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert Executor(Context(None)).execute(UninstallSudoers())
    assert fake_runner.calls == [("sudo", "rm", str(policy))]


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_nonregular_policy_is_refused_even_with_force(policy, kind):
    if kind == "directory":
        policy.mkdir()
    else:
        policy.symlink_to(policy.with_name("travel-mode-test"))
    with pytest.raises(sudoers.SudoersError):
        Executor(Context(None)).execute(InstallSudoers(force=True))


def test_status_reads_content_and_only_probes_permissions(policy, fake_runner):
    policy.write_text("# example policy\n")
    data = sudoers.status()
    assert data["installed"] and data["content"] == "# example policy\n"
    assert len(data["probes"]) == len(PRIVILEGES)
    assert all(c[:3] == ("sudo", "-n", "-l") for c in fake_runner.calls)


def test_cli_dry_run_previews_and_print_does_not_probe(policy, darwin_platform, fake_runner, capsys):
    assert cli.main(["sudoers", "print", "--user", "example_user", "--lang", "en"]) == 0
    assert not fake_runner.calls
    assert cli.main(["sudoers", "install", "--dry-run", "--lang", "en"]) == 0
    output = capsys.readouterr().out
    assert "[dry-run]" in output and "install -m 0440 -o root -g wheel" in output
    assert not any(c[:2] == ("sudo", "install") for c in fake_runner.calls)
