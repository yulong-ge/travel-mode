"""Health presentation and remediation through the existing Action boundary."""

from ..actions.apps import FreezeApp, QuitApp, QuitDaemonApp, configured_daemon
from ..actions.power import EnableSleep, KillCaffeinate, SetBrightness, SetPowerProfile, StopSpotlight
from ..i18n import t
from ..system.health import collect as health_collect
from ..system.process_control import Process
from ..system.processes import CaffeinateTarget


def lines(report, *, verbose=False, scenario=None):
    from ..config import load_config
    from ..health_view import presentation

    if verbose:
        yield t("health.timing", seconds=report["elapsed_seconds"], budget=report["budget_seconds"])
    for row in presentation(report, scenario=scenario or load_config().options.scenario):
        yield row["summary"]
        if verbose:
            for line in row["details"]:
                yield "  " + line
            for reference in row["references"]:
                yield "  " + t("health.see", title=reference)
            if row["error"]:
                yield "  " + row["error"]
            for evidence in row["evidence"]:
                yield f"  {evidence}"
        if row["state"] == "action":
            if verbose and row["impact"]:
                yield f"  {row['impact']}"
            if verbose and row["manual"]:
                yield f"  {row['manual']}"
            for action in row["actions"] if verbose else row["display_actions"]:
                yield ("  " if verbose else " ") + action_label(action)
    if verbose:
        yield from report["limitations"]


def action_label(descriptor):
    if descriptor["kind"] == "brightness":
        return t("health.action.brightness", percent=round(float(descriptor["value"]) * 100))
    return t(
        "health.action." + descriptor["kind"],
        name=descriptor.get("name", ""),
        pid=descriptor.get("process", {}).get("pid", ""),
        value=descriptor.get("value", ""),
    )


def make_action(descriptor, state, save):
    kind = descriptor["kind"]
    if kind == "sleep":
        return EnableSleep()
    if kind == "spotlight":
        return StopSpotlight()
    if kind == "low_power":
        return SetPowerProfile([descriptor["key"], "1"])
    if kind == "brightness":
        return SetBrightness(descriptor["value"])
    if kind == "caffeinate":
        row = descriptor["process"]
        process = Process(row["pid"], row["ppid"], row["stat"], row["started"], row["executable"])
        return KillCaffeinate(CaffeinateTarget(process, descriptor["command_line"], descriptor["parent"]))
    if kind == "quit":
        spec = configured_daemon(descriptor["name"])
        if spec:
            return QuitDaemonApp(spec, state, save)
        return QuitApp(descriptor["path"], descriptor["name"])
    if kind == "freeze":
        return FreezeApp(descriptor["path"])
    raise ValueError(f"Unknown health action: {kind}")


def remediate(descriptor, execute, state, save):
    result = execute(make_action(descriptor, state, save))
    if isinstance(result, tuple):
        success = result[1] if len(result) == 3 else result[1] == 0
    else:
        success = bool(result)
    if not success:
        return False
    save()
    return True


__all__ = ["collect", "lines", "make_action", "remediate"]


def collect():
    from ..sleep_readiness import from_report, store

    report = health_collect()
    store(from_report(report))
    return report
