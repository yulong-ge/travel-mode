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
        choices=[
            "on",
            "off",
            "restore",
            "check",
            "report",
            "config",
            "thaw",
            "profiles",
            "suggest",
            "power",
            "history",
            "automation",
            "event",
            "sudoers",
            "status",
            "menubar",
        ],
    )
    parser.add_argument(
        "config_cmd",
        nargs="?",
        choices=[
            "path",
            "init",
            "validate",
            "edit",
            "set-language",
            "set-interface",
            "install",
            "uninstall",
            "status",
            "power-changed",
            "print",
        ],
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
    parser.add_argument("--scope", choices=["pmset", "all"])
    parser.add_argument("--user", dest="sudoers_user")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--limit", type=int, help=t("session.limit_help"))
    parser.add_argument("--seconds", type=float, help=t("measurement.seconds_help"))
    parser.add_argument("--updates", type=int, help=t("measurement.updates_help"))
    measure_flags = parser.add_mutually_exclusive_group()
    measure_flags.add_argument(
        "--measure", dest="measure", action="store_true", default=None, help=t("measurement.enable_help")
    )
    measure_flags.add_argument(
        "--no-measure", dest="measure", action="store_false", help=t("measurement.disable_help")
    )
    parser.add_argument("--reapply", action="store_true", help=t("resurrection.reapply_help"))
    parser.add_argument("--lang", choices=["en", "zh"], help=t("cli.output_language"))
    parser.add_argument("--demo", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--launch-event", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--all", action="store_true", help=t("cli.thaw_resume_all_stopped_app_processes"))
    parser.add_argument("--version", action="version", version="travel-mode 1.1.0")
    args = parser.parse_args(argv)
    if args.config_cmd is not None and args.cmd not in {
        "config",
        "automation",
        "event",
        "sudoers",
        "menubar",
    }:
        parser.error(t("cli.unexpected_config_value"))
    if args.scope is not None and (args.cmd != "sudoers" or args.config_cmd not in {"print", "install"}):
        parser.error(t("sudoers.usage"))
    if args.sudoers_user is not None and (args.cmd, args.config_cmd) != ("sudoers", "print"):
        parser.error(t("sudoers.usage"))
    if args.cmd == "config" and args.config_cmd not in {
        None,
        "path",
        "init",
        "validate",
        "edit",
        "set-language",
        "set-interface",
    }:
        parser.error(t("cli.config_requires_path_init_validate_or"))
    if args.launch_event and (args.cmd, args.config_cmd) != ("event", "power-changed"):
        parser.error(t("automation.event_usage"))
    if args.cmd == "event":
        if args.config_cmd != "power-changed" or args.value is not None or args.dry_run or args.demo:
            parser.error(t("automation.event_usage"))
        from .flows.automation import event
        from .system.platform import ensure_macos

        ensure_macos()
        return event(launch_event=args.launch_event)
    if args.limit is not None and (args.cmd != "history" or args.limit < 1):
        parser.error(t("session.limit_error"))
    if args.seconds is not None and args.cmd != "power":
        parser.error(t("measurement.seconds_only_power"))
    if args.updates is not None and args.cmd != "power":
        parser.error(t("measurement.updates_only_power"))
    if args.reapply and args.cmd != "check" and not args.check:
        parser.error(t("resurrection.check_only"))
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
    exempt = args.cmd == "history" or (args.cmd == "config" and args.config_cmd in {"path", "validate"})
    exempt = exempt or (args.cmd, args.config_cmd) == ("sudoers", "print")
    if not exempt:
        from .system.platform import detect_capabilities, ensure_macos

        try:
            ensure_macos()
            if not args.demo and args.cmd not in {"automation", "sudoers", "status", "menubar"}:
                detect_capabilities()
        except SystemExit:
            raise
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    migration_messages = (
        []
        if args.cmd in {"suggest", "history", "automation", "sudoers", "status", "menubar"}
        else migrate_legacy()
    )
    for message in migration_messages:
        print(message, file=sys.stderr)
    if args.cmd == "status":
        import json

        from .status import StatusError, data
        from .system.shell import ReadError

        try:
            result = data()
        except ReadError as exc:
            raise StatusError(str(exc)) from exc
        print(
            json.dumps(result, ensure_ascii=False)
            if args.json
            else result["swiftbar"].split("---")[1].strip()
        )
        return 0
    if args.cmd == "menubar":
        import json

        from . import menubar
        from .actions.base import Context
        from .actions.executor import Executor
        from .actions.menubar import InstallMenubar, UninstallMenubar
        from .presentation import yes_no

        if args.config_cmd == "status":
            result = menubar.status()
            labels = {key: yes_no(result[key]) for key in ("installed", "current", "executable")}
            print(
                json.dumps(result, ensure_ascii=False)
                if args.json
                else t("menubar.plugin_status", **{**result, **labels})
            )
            return 0
        if args.config_cmd not in {"install", "uninstall"} or args.json:
            parser.error(t("menubar.usage"))
        action = InstallMenubar(args.force) if args.config_cmd == "install" else UninstallMenubar()
        Executor(Context(None)).execute(action)
        if args.dry_run:
            print(t("menubar.planned"))
        return 0
    if args.cmd == "sudoers":
        import json

        from . import sudoers
        from .actions.base import Context
        from .actions.executor import Executor
        from .actions.sudoers import InstallSudoers, UninstallSudoers

        if args.config_cmd == "print" and not args.json:
            print(sudoers.snippet(args.scope or "pmset", args.sudoers_user), end="")
            return 0
        if args.config_cmd == "status":
            result = sudoers.status()
            print(json.dumps(result, ensure_ascii=False, indent=None if args.json else 2))
            return 1 if result["content_error"] else 0
        if args.config_cmd not in {"install", "uninstall"} or args.json:
            parser.error(t("sudoers.usage"))
        action = (
            InstallSudoers(args.scope or "pmset", args.force)
            if args.config_cmd == "install"
            else UninstallSudoers()
        )
        Executor(Context(None)).execute(action)
        print(t("sudoers.planned" if args.dry_run else "sudoers.completed"))
        return 0
    from .tui import app as tui_app

    if args.demo:
        demo._configure_demo_views()
    mode_apply, mode_check, mode_off = flow_apply.mode_apply, flow_check.mode_check, flow_restore.mode_off
    mode_report, thaw_processes, tui = flow_report.mode_report, flow_thaw.thaw_processes, tui_app.tui

    if args.cmd == "automation":
        import json

        from . import automation
        from .actions.automation import InstallAutomation, UninstallAutomation
        from .actions.base import Context
        from .actions.executor import Executor
        from .presentation import yes_no

        if args.config_cmd == "status":
            result = automation.status()
            print(
                json.dumps(result, ensure_ascii=False)
                if args.json
                else t(
                    "automation.status",
                    installed=yes_no(result["installed"]),
                    loaded=yes_no(result["loaded"]),
                    running=yes_no(result["running"]),
                    path=result["path"],
                )
                + "\n"
                + json.dumps(
                    {"plist": result["plist"], "last_event": result["last_event"]},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.config_cmd not in {"install", "uninstall"} or args.json:
            parser.error(t("automation.usage"))
        action = InstallAutomation() if args.config_cmd == "install" else UninstallAutomation()
        Executor(Context(None)).execute(action)
        print(t("automation.planned" if args.dry_run else "automation.completed", action=action.describe()))
        return 0

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
    if args.cmd == "history":
        import json

        from . import sessions

        rows = sessions.read_history()
        limit = args.limit or 50
        selected = list(reversed(rows))[:limit]
        if args.json:
            print(
                json.dumps(
                    {
                        "version": 1,
                        "sessions": [
                            {**row, "comparison": sessions.comparison(row, rows)} for row in selected
                        ],
                        "limit": limit,
                    },
                    ensure_ascii=False,
                )
            )
        elif not selected:
            print(t("session.history_empty"))
        else:
            for row in selected:
                print("\n".join(sessions.describe(row, rows)))
        return 0
    if args.cmd == "power":
        import json
        from dataclasses import replace

        from .measurement import describe, measure, validate_settings
        from .system.battery import BatteryError
        from .system.shell import ReadError

        settings = load_config().measurement
        if args.seconds is not None:
            settings = replace(settings, seconds=args.seconds)
        if args.updates is not None:
            settings = replace(settings, updates=args.updates)
        try:
            validate_settings(settings)
        except ValueError as exc:
            parser.error(str(exc))
        try:
            result = measure(settings)
        except ReadError as exc:
            raise BatteryError(str(exc)) from exc
        print(json.dumps(result, ensure_ascii=False) if args.json else describe(result))
        return (
            4
            if result.get("reason")
            in {"insufficient_samples", "counter_reset", "missing_update_marker", "source_changed"}
            else 0
        )
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
        reapply_failed = 0
        if args.reapply:
            from .flows.check import reapply_paused

            reapply_failed = reapply_paused()
        if args.dry_run:
            from .actions.runtime import _prepare_dry_run

            _prepare_dry_run(True)
        if args.json:
            import json

            from .structured import check_data

            print(json.dumps(check_data(), ensure_ascii=False))
            return int(bool(reapply_failed))
        return mode_check() or int(bool(reapply_failed))
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
                return mode_apply(
                    yes=args.yes, aggressive=args.aggressive, dry_run=args.dry_run, measure=args.measure
                )
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                print(t("impl.non_interactive_terminal_running_check_use"), file=sys.stderr)
                return mode_check()
            if interface == "cli":
                return mode_apply(
                    yes=False, aggressive=args.aggressive, dry_run=args.dry_run, measure=args.measure
                )
            if args.dry_run:
                from .actions.runtime import _prepare_dry_run

                _prepare_dry_run(True)
            from .state import load_state

            load_state()  # Validate before Textual can wrap startup errors in a worker exception.
            return tui(dry_run=args.dry_run, measure=args.measure)
    raise RuntimeError("unhandled travel-mode command")


__all__ = ["main"]


def _handled_main(argv=None):
    from contextlib import nullcontext

    from .automation import AutomationError, preview_scope
    from .failures import CommandError
    from .menubar import MenubarError
    from .menubar import preview_scope as menubar_preview_scope
    from .paths import state_path
    from .state import StateError
    from .status import StatusError
    from .sudoers import SudoersError
    from .system.battery import BatteryError
    from .system.shell import ReadError, SudoReadError

    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        preview = nullcontext()
        if "--dry-run" in arguments:
            if "automation" in arguments:
                preview = preview_scope()
            elif "menubar" in arguments:
                preview = menubar_preview_scope()
        with preview:
            return command_lifecycle(arguments, _main)
    except (SudoersError, StatusError, MenubarError) as exc:
        if os.environ.get("TRAVEL_MODE_DEBUG") == "1":
            raise
        detail = " ".join(str(exc).splitlines())
        if "--json" in arguments:
            import json

            result = {"error": {"type": type(exc).__name__, "message": detail}}
            if "status" in arguments and "menubar" not in arguments and "sudoers" not in arguments:
                from .status import error_menu

                result["swiftbar"] = error_menu(detail)
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(detail, file=sys.stderr)
        return 6 if isinstance(exc, SudoersError) else 1
    except AutomationError as exc:
        if os.environ.get("TRAVEL_MODE_DEBUG") == "1":
            raise
        print(t("automation.failed", detail=" ".join(str(exc).splitlines())), file=sys.stderr)
        return 5
    except BatteryError as exc:
        if os.environ.get("TRAVEL_MODE_DEBUG") == "1":
            raise
        detail = " ".join(str(exc).splitlines())
        if "--json" in arguments:
            import json

            print(
                json.dumps(
                    {
                        "available": False,
                        "reason": "failed",
                        "error": {"type": "BatteryError", "message": detail},
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(t("measurement.failed", reason=detail), file=sys.stderr)
        return 4
    except (CommandError, ReadError, SudoReadError) as exc:
        if os.environ.get("TRAVEL_MODE_DEBUG") == "1":
            raise
        from . import failures

        commands = (
            [dict(command=exc.command, rc=exc.rc, stdout="", stderr=exc.detail)]
            if isinstance(exc, CommandError)
            else []
        )
        row = failures.record(None, " ".join(arguments[:2]), str(exc), commands)
        print(failures.message(row), file=sys.stderr)
        return 1
    except (ConfigError, StateError) as exc:
        if os.environ.get("TRAVEL_MODE_DEBUG") == "1":
            raise
        detail = " ".join(str(exc).splitlines())
        if isinstance(exc, ConfigError):
            print(f"{detail}. {t('errors.config_hint')}", file=sys.stderr)
            return 2
        location = "" if str(state_path()) in detail else f"{state_path()}: "
        from . import failures

        if not getattr(exc, "travel_failure", None):
            try:
                failures.record(None, " ".join(arguments[:2]), detail, error=exc, kind="state")
            except StateError as log_error:
                detail += f". {log_error}"
        print(
            f"{location}{detail}. {t('errors.state_hint')} {t('failure.log_path', path=failures.log_path())}",
            file=sys.stderr,
        )
        return 3


def ask_scenario():
    print(t("scenario.prompt"))
    print(f"  work: {t('scenario.work_desc')}")
    print(f"  away: {t('scenario.away_desc')}")
    try:
        answer = input(t("scenario.input")).strip().lower()
    except EOFError as exc:
        raise ConfigError("<cli>", "--scenario", t("scenario.required")) from exc
    if answer not in ("work", "away"):
        raise ConfigError("<cli>", "--scenario", t("scenario.invalid"))
    return answer


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not {"event", "power-changed", "--launch-event"} <= set(arguments):
        return _handled_main(arguments)
    # libxpc has no unregister API. Native callbacks must not race CPython's
    # interpreter teardown. Complete all scoped cleanup, flush, then exit the
    # one-shot launchd child without running interpreter finalizers.
    try:
        result = _handled_main(arguments)
    except BaseException:  # noqa: BLE001 - native child reports then exits without interpreter teardown.
        import traceback

        traceback.print_exc()
        result = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(result)
