"""Command line interface."""

from __future__ import annotations

import argparse
import os
import sys

import travel_mode.flows.apply as flow_apply
import travel_mode.flows.check as flow_check
import travel_mode.flows.report as flow_report
import travel_mode.flows.restore as flow_restore
import travel_mode.flows.thaw as flow_thaw

from . import demo
from .actions.runtime import command_lifecycle
from .config import ConfigError, load_config, migrate_legacy, select_interface
from .flows import configuration
from .i18n import select_language, set_language, t


def _main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    early_lang = None
    if "--lang" in raw_argv:
        index = raw_argv.index("--lang")
        if index + 1 < len(raw_argv):
            early_lang = raw_argv[index + 1]
    set_language(select_language(early_lang, "auto"))
    parser = argparse.ArgumentParser(
        prog="travel-mode", description=t("impl.macos_battery_saving_travel_mode_python")
    )
    parser.add_argument(
        "cmd",
        nargs="?",
        choices=["on", "off", "restore", "check", "report", "config", "thaw", "profiles", "suggest"],
    )
    parser.add_argument(
        "config_cmd", nargs="?", choices=["path", "init", "validate", "edit", "set-language", "set-interface"]
    )
    parser.add_argument("value", nargs="?")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--yes", "-y", action="store_true")
    interface_flags = parser.add_mutually_exclusive_group()
    interface_flags.add_argument("--no-tui", dest="interface", action="store_const", const="cli")
    interface_flags.add_argument("--tui", dest="interface", action="store_const", const="tui")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--aggressive", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--scenario", choices=["work", "away"], help=t("scenario.help"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--lang", choices=["en", "zh"], help=t("cli.output_language"))
    parser.add_argument("--demo", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--all", action="store_true", help=t("cli.thaw_resume_all_stopped_app_processes"))
    parser.add_argument("--version", action="version", version="travel-mode 1.1.0")
    args = parser.parse_args(argv)
    if args.value is not None and args.config_cmd not in {"set-language", "set-interface"}:
        parser.error(t("cli.unexpected_config_value"))
    if args.config_cmd == "set-language" and args.value not in {"en", "zh"}:
        parser.error(t("config.must_be_auto_zh_or_en"))
    configured = load_config().options
    set_language(select_language(args.lang, configured.language))
    interface = select_interface(args.interface, configured.interface)
    if args.demo:
        os.environ["TRAVEL_MODE_DEMO"] = "1"
        demo._configure_demo()
    exempt = args.cmd == "config" and args.config_cmd in {"path", "validate"}
    if not exempt:
        from .system.platform import detect_capabilities, ensure_macos

        try:
            ensure_macos()
            if not args.demo:
                detect_capabilities()
        except SystemExit:
            raise
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    migration_messages = [] if args.cmd == "suggest" else migrate_legacy()
    for message in migration_messages:
        print(message, file=sys.stderr)
    from .tui import app as tui_app

    if args.demo:
        demo._configure_demo_views()
    mode_apply, mode_check, mode_off = flow_apply.mode_apply, flow_check.mode_check, flow_restore.mode_off
    mode_report, thaw_processes, tui = flow_report.mode_report, flow_thaw.thaw_processes, tui_app.tui

    if args.cmd == "config":
        if not args.config_cmd:
            parser.error(t("cli.config_requires_path_init_validate_or"))
        return configuration._config_command(args)
    if args.cmd == "profiles":
        from .profiles import builtin_profiles

        config = load_config()
        for name, profile in {**builtin_profiles(), **config.profiles}.items():
            print(f"{name}: {profile.values.get('description', '')}")
        return 0
    if args.cmd == "suggest":
        from .flows.suggest import confirm_write, print_suggestions, print_warnings, suggest_data

        data = suggest_data(args.samples)
        print_warnings(data)
        if args.json:
            import json

            print(json.dumps(data, ensure_ascii=False))
        else:
            print_suggestions(data)
        if args.write:
            confirm_write(data)
        return 0
    if args.cmd in ("off", "restore"):
        return mode_off(force_off=args.force, dry_run=args.dry_run)
    if args.cmd == "thaw":
        return thaw_processes(all_processes=args.all)
    if args.cmd == "check" or args.check:
        if args.dry_run:
            from .actions.runtime import _prepare_dry_run

            _prepare_dry_run(True)
        if args.json:
            import json

            from .structured import check_data

            print(json.dumps(check_data(), ensure_ascii=False))
            return 0
        return mode_check()
    if args.cmd == "report" or args.report:
        if args.dry_run:
            from .actions.runtime import _prepare_dry_run

            _prepare_dry_run(True)
        if args.json:
            import json

            from .structured import report_data

            print(json.dumps(report_data(), ensure_ascii=False))
            return 0
        return mode_report()
    from .profiles import profile_scope

    if args.yes or args.interface is not None or args.cmd in (None, "on"):
        config = load_config()
        scenario = args.scenario
        if (
            args.yes
            or args.interface == "cli"
            or (interface == "cli" and sys.stdin.isatty() and sys.stdout.isatty())
        ) and not args.profile:
            scenario = scenario or config.options.scenario
            if scenario == "ask":
                if args.yes:
                    raise ConfigError("<cli>", "--scenario", t("scenario.required"))
                scenario = ask_scenario()
        with profile_scope(config, args.profile, aggressive=args.aggressive, scenario=scenario):
            if args.yes or args.interface == "cli":
                return mode_apply(yes=args.yes, aggressive=args.aggressive, dry_run=args.dry_run)
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                print(t("impl.non_interactive_terminal_running_check_use"), file=sys.stderr)
                return mode_check()
            if interface == "cli":
                return mode_apply(yes=False, aggressive=args.aggressive, dry_run=args.dry_run)
            if args.dry_run:
                from .actions.runtime import _prepare_dry_run

                _prepare_dry_run(True)
            from .state import load_state

            load_state()  # Validate before Textual can wrap startup errors in a worker exception.
            return tui(dry_run=args.dry_run)
    raise RuntimeError("unhandled travel-mode command")


__all__ = ["main"]


def main(argv=None):
    from .paths import state_path
    from .state import StateError

    try:
        return command_lifecycle(list(sys.argv[1:] if argv is None else argv), _main)
    except (ConfigError, StateError) as exc:
        if os.environ.get("TRAVEL_MODE_DEBUG") == "1":
            raise
        detail = " ".join(str(exc).splitlines())
        if isinstance(exc, ConfigError):
            print(f"{detail}. {t('errors.config_hint')}", file=sys.stderr)
            return 2
        location = "" if str(state_path()) in detail else f"{state_path()}: "
        print(f"{location}{detail}. {t('errors.state_hint')}", file=sys.stderr)
        return 3


def ask_scenario():
    print(t("scenario.prompt"))
    print(f"  work: {t('scenario.work_desc')}")
    print(f"  away: {t('scenario.away_desc')}")
    try:
        answer = input("  [work/away] ").strip().lower()
    except EOFError as exc:
        raise ConfigError("<cli>", "--scenario", t("scenario.required")) from exc
    if answer not in ("work", "away"):
        raise ConfigError("<cli>", "--scenario", t("scenario.invalid"))
    return answer
