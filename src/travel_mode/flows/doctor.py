"""Health presentation and remediation through the existing Action boundary."""

from ..actions.apps import FreezeApp, QuitApp, QuitDaemonApp, configured_daemon
from ..actions.power import EnableSleep, KillCaffeinate, SetBrightness, SetPowerProfile, StopSpotlight
from ..i18n import t
from ..system.health import collect
from ..system.process_control import Process
from ..system.processes import CaffeinateTarget


def lines(report):
    from ..health_view import display_checks

    yield t("health.timing", seconds=report["elapsed_seconds"], budget=report["budget_seconds"])
    for row in display_checks(report):
        yield f"{row['title']}: {t('health.' + row['status'])}"
        if row["error"]:
            yield f"  {row['error']}"
        for reference in row["references"]:
            yield "  " + t("health.see", title=reference)
        for evidence in row["evidence"]:
            yield f"  {evidence}"
        if row["status"] == "hit":
            if row["impact"]:
                yield f"  {row['impact']}"
            if row["manual"]:
                yield f"  {row['manual']}"
            for action in row["actions"]:
                yield f"  {action_label(action)}"
    yield from report["limitations"]


def action_label(descriptor):
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
        raise RuntimeError(t("health.action_failed", action=action_label(descriptor)))
    save()


__all__ = ["collect", "lines", "make_action", "remediate"]
