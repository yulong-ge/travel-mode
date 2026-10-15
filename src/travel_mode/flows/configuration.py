"""Configuration subcommands."""

from __future__ import annotations

import argparse
import os
import sys

from travel_mode.config import load_config, write_interface, write_language
from travel_mode.i18n import t
from travel_mode.paths import config_path, state_path


def _template() -> str:
    from importlib.resources import files

    return files("travel_mode").joinpath("config_template.toml").read_text(encoding="utf-8")


def _config_command(args) -> int:
    target = config_path()
    if args.config_cmd == "path":
        status = t("cli.exists") if target.exists() else t("cli.missing")
        print(t("cli.config_path_status", path=target, status=status))
        current_state = state_path()
        state_status = t("cli.exists") if current_state.exists() else t("cli.missing")
        print(t("cli.state_path_status", path=current_state, status=state_status))
        return 0
    if args.config_cmd == "init":
        if target.exists() and not args.force:
            print(t("cli.configuration_exists_use_force_to_overwrite", v0=target), file=sys.stderr)
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_template(), encoding="utf-8")
        print(t("cli.configuration_template_written", v0=target))
        return 0
    if args.config_cmd == "validate":
        config = load_config()
        counts = {action: len(getattr(config.apps, action)) for action in ("quit", "freeze", "skip", "ask")}
        print(
            t("cli.configuration_valid")
            + ", ".join(f"{key}={value}" for key, value in counts.items())
            + f", services={len(config.services.unload)}"
        )
        return 0
    if args.config_cmd in {"set-language", "set-interface"}:
        option = args.config_cmd.removeprefix("set-")
        writer = write_language if option == "language" else write_interface
        writer(args.value)
        if option == "language":
            print(t("language.saved", language=args.value, path=target))
        else:
            print(t("interface.saved", interface=args.value, path=target))
        return 0
    if args.config_cmd == "edit":
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if not editor:
            print(t("cli.visual_or_editor_is_not_set"), file=sys.stderr)
            return 1
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_template(), encoding="utf-8")
        from ..system.shell import run_rc

        _, returncode = run_rc([editor, str(target)], interactive=True)
        if returncode != 0:
            return returncode
        return _config_command(argparse.Namespace(config_cmd="validate", force=False))
    raise ValueError(t("cli.unknown_config_subcommand", v0=args.config_cmd))
