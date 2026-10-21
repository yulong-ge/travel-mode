"""runtime implementation."""

from __future__ import annotations

import os

from travel_mode.i18n import t
from travel_mode.system.shell import is_dry_run

from ..paths import config_path, state_path
from .executor import _sudo_lifecycle as _sudo_lifecycle


def _prepare_dry_run(enabled: bool) -> None:
    if not enabled:
        return
    from ..state import set_state_path_override
    from ..system.shell import enable_dry_run

    directory = enable_dry_run()
    set_state_path_override(directory / "state.json")


def _finish_dry_run() -> None:
    if not is_dry_run():
        return
    from ..state import set_state_path_override
    from ..system.shell import dry_run_commands, dry_run_state_dir, dry_run_unknowns

    print(t("impl.commands_would_be_executed", v0=len(dry_run_commands())))
    print(t("impl.temporary_state_path", v0=dry_run_state_dir() / "state.json"))
    for detail in dry_run_unknowns():
        print(t("sudo.unknown", detail=detail))
    set_state_path_override(None)
    from ..system.shell import disable_dry_run

    disable_dry_run()


def command_lifecycle(argv, dispatch):
    """Keep every dry-run write and legacy migration inside one isolated lifecycle."""
    raw_argv = list(argv)
    dry_run = "--dry-run" in raw_argv
    if not dry_run and "--demo" not in raw_argv:
        return dispatch(raw_argv)

    import shutil

    from .. import state
    from ..paths import legacy_conf_path, legacy_state_path
    from ..system.launchd import discovery_scope, scan_directories
    from ..system.shell import disable_dry_run, enable_dry_run, get_runner, set_runner

    sources = {
        "config.toml": config_path(),
        "state.json": state_path(),
        "home/.travel-mode.conf": legacy_conf_path(),
        "home/.travel-mode.state": legacy_state_path(),
    }
    variables = ("HOME", "TRAVEL_MODE_CONFIG", "TRAVEL_MODE_STATE", "TRAVEL_MODE_DEMO")
    previous_env = {name: os.environ.get(name) for name in variables}
    previous_override = state._STATE_PATH_OVERRIDE
    previous_runner = get_runner()
    discovery_roots = scan_directories() if dry_run else ()
    if dry_run:
        directory = enable_dry_run()
    else:
        import tempfile
        from pathlib import Path

        temporary = tempfile.TemporaryDirectory(prefix="travel-mode-demo-")
        directory = Path(temporary.name)
    try:
        for relative, source in sources.items():
            if source.exists():
                destination = directory / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        (directory / "home").mkdir(exist_ok=True)
        os.environ["HOME"] = str(directory / "home")
        os.environ["TRAVEL_MODE_CONFIG"] = str(directory / "config.toml")
        os.environ["TRAVEL_MODE_STATE"] = str(directory / "state.json")
        state.set_state_path_override(directory / "state.json")
        with discovery_scope(discovery_roots):
            result = dispatch(raw_argv)
        from ..system.shell import dry_run_unknowns

        return (result or 1) if dry_run and dry_run_unknowns() else result
    finally:
        try:
            disable_dry_run()
            set_runner(previous_runner)
            state.set_state_path_override(previous_override)
            for name, value in previous_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        finally:
            if not dry_run:
                temporary.cleanup()
