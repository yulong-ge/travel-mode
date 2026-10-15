"""Interface selection and prompts use isolated files and the strict FakeRunner."""

import sys
from pathlib import Path

import pytest

import travel_mode.flows.apply as flow_apply
import travel_mode.flows.check as flow_check
from travel_mode.cli import main
from travel_mode.config import ConfigError, load_config, select_interface, write_interface
from travel_mode.i18n import set_language, t
from travel_mode.paths import config_path
from travel_mode.tui import app as tui_app


@pytest.mark.parametrize("interface", ["cli", "tui"])
def test_interface_config_and_missing_default(tmp_path, interface):
    assert load_config(tmp_path / "missing").options.interface == "tui"
    path = tmp_path / "config.toml"
    path.write_text(f'[options]\ninterface = "{interface}"\n')
    assert load_config(path).options.interface == interface


@pytest.mark.parametrize("value", ['"invalid"', '""', "1", "true", "[]", "{}"])
def test_interface_config_invalid_value_has_key_path(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(f"[options]\ninterface = {value}\n")
    with pytest.raises(ConfigError, match=r"options\.interface") as error:
        load_config(path)
    assert error.value.path == str(path)


@pytest.mark.parametrize(
    "cli, env, configured, expected",
    [
        (None, None, "tui", "tui"),
        (None, None, "cli", "cli"),
        (None, "cli", "tui", "cli"),
        (None, "tui", "cli", "tui"),
        ("tui", "cli", "cli", "tui"),
        ("cli", "tui", "tui", "cli"),
    ],
)
def test_interface_priority(cli, env, configured, expected, monkeypatch):
    if env is not None:
        monkeypatch.setenv("TRAVEL_MODE_INTERFACE", env)
    assert select_interface(cli, configured) == expected


@pytest.mark.parametrize("value", ["invalid", "", "CLI"])
def test_interface_environment_invalid_value_is_explicit(value, monkeypatch):
    monkeypatch.setenv("TRAVEL_MODE_INTERFACE", value)
    with pytest.raises(ConfigError, match=r"TRAVEL_MODE_INTERFACE: options\.interface"):
        main(["check"])


@pytest.mark.parametrize("interface", ["cli", "tui"])
@pytest.mark.parametrize("existing", [True, False])
def test_set_interface_preserves_comments_and_other_options(interface, existing, darwin_platform, capsys):
    path = config_path()
    if existing:
        path.write_text('# heading\n[options] # options\nlanguage = "en" # keep\ninterface = "tui" # UI\n')
    assert main(["config", "set-interface", interface]) == 0
    assert load_config(path).options.interface == interface
    if existing:
        assert path.read_text() == (
            f'# heading\n[options] # options\nlanguage = "en" # keep\ninterface = "{interface}" # UI\n'
        )
    assert str(path) in capsys.readouterr().out


@pytest.mark.parametrize("value", [None, "invalid", "CLI"])
def test_set_interface_invalid_value_does_not_write(value, darwin_platform, capsys):
    path = config_path()
    path.write_text('# unchanged\n[options]\nlanguage = "en"\n')
    before = path.read_bytes()
    argv = ["config", "set-interface"] + ([] if value is None else [value])
    assert main(argv) == 2
    assert "options.interface" in capsys.readouterr().err
    assert path.read_bytes() == before


@pytest.mark.parametrize("existing", [True, False])
def test_set_interface_dry_run_writes_only_isolated_copy(existing, darwin_platform, capsys):
    path = config_path()
    if existing:
        path.write_text('# original\n[options]\ninterface = "tui" # UI\n')
    before = (path.read_bytes(), path.stat().st_mtime_ns) if existing else None
    assert main(["config", "set-interface", "cli", "--dry-run"]) == 0
    saved_path = Path(capsys.readouterr().out.strip().rsplit(": ", 1)[1])
    assert saved_path != path
    assert load_config(saved_path).options.interface == "cli"
    if existing:
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before
        assert "# original" in saved_path.read_text() and "# UI" in saved_path.read_text()
    else:
        assert not path.exists()


def test_write_interface_replace_failure_preserves_original(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('# original\n[options]\ninterface = "tui"\n')
    before = path.read_bytes()

    def fail_replace(self, target):
        raise OSError("fixture replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(ConfigError, match=r"options\.interface") as error:
        write_interface("cli", path)
    assert isinstance(error.value.__cause__, OSError)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("command", [[], ["on"]])
@pytest.mark.parametrize("source", ["config", "environment"])
def test_cli_interface_entry_runs_scripted_prompts(
    language, command, source, darwin_platform, monkeypatch, fake_runner, capsys
):
    if source == "config":
        config_path().write_text('[options]\ninterface = "cli"\n')
    else:
        monkeypatch.setenv("TRAVEL_MODE_INTERFACE", "cli")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    answers = iter(["n"] * 64)
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)
    assert main([*command, "--lang", language]) == 0
    assert prompts and any("[Y/n]" in prompt for prompt in prompts)
    assert capsys.readouterr().out.splitlines()[0] == t("cli.prompt_help")
    assert ("pmset", "-g", "custom") in fake_runner.calls
    assert not any(call[:2] == ("sudo", "pmset") for call in fake_runner.calls)


@pytest.mark.parametrize("tty", [(False, True), (True, False), (False, False)])
@pytest.mark.parametrize("command", [[], ["on"]])
@pytest.mark.parametrize("source", ["config", "environment"])
def test_cli_interface_noninteractive_falls_back_to_check(
    tty, command, source, darwin_platform, monkeypatch, capsys
):
    if source == "config":
        config_path().write_text('[options]\ninterface = "cli"\n')
    else:
        monkeypatch.setenv("TRAVEL_MODE_INTERFACE", "cli")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: tty[0])
    monkeypatch.setattr(sys.stdout, "isatty", lambda: tty[1])
    checks = []
    monkeypatch.setattr(flow_check, "mode_check", lambda: checks.append("check") or 0)
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("Non-interactive prompt"))
    assert main(command) == 0
    assert checks == ["check"]
    assert "non-interactive" in capsys.readouterr().err.lower()


@pytest.mark.parametrize(
    "flags, environment, configured, expected",
    [
        ([], None, None, "tui"),
        ([], "tui", "cli", "tui"),
        ([], "cli", "tui", "cli"),
        (["--tui"], "cli", "cli", "tui"),
        (["--no-tui"], "tui", "tui", "cli"),
        (["--yes"], "tui", "tui", "yes"),
    ],
)
def test_interface_flags_dispatch(flags, environment, configured, expected, darwin_platform, monkeypatch):
    if configured is not None:
        config_path().write_text(f'[options]\ninterface = "{configured}"\n')
    if environment is not None:
        monkeypatch.setenv("TRAVEL_MODE_INTERFACE", environment)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    calls = []
    monkeypatch.setattr(tui_app, "tui", lambda **kwargs: calls.append("tui") or 0)
    monkeypatch.setattr(
        flow_apply, "mode_apply", lambda **kwargs: calls.append("yes" if kwargs["yes"] else "cli") or 0
    )
    assert main(["on", *flags]) == 0
    assert calls == [expected]


def test_interface_flags_are_mutually_exclusive():
    with pytest.raises(SystemExit) as error:
        main(["--tui", "--no-tui"])
    assert error.value.code == 2


@pytest.mark.parametrize("language", ["en", "zh"])
def test_prompt_help_is_translated_and_contains_controls(language):
    set_language(language)
    help_text = t("cli.prompt_help")
    for control in ("[Y/n]", "--yes", "Ctrl-C", "travel-mode off"):
        assert control in help_text
    assert ("回车" in help_text) == (language == "zh")


def test_config_templates_include_documented_interface_default():
    root = Path(__file__).resolve().parents[2]
    template = root / "src/travel_mode/config_template.toml"
    example = root / "examples/config.toml"
    assert template.read_bytes() == example.read_bytes()
    assert load_config(template).options.interface == "tui"
    assert "# Default interface:" in template.read_text()
    assert "TRAVEL_MODE_INTERFACE" in template.read_text()
