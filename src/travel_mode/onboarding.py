"""One finite recommendation snapshot shared by setup clients and suggestion writes."""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

from . import app_policy, categories, sleep_readiness
from .config import load_config
from .i18n import t
from .system import launchd, pmset, shell
from .system.app_snapshot import snapshot_scope

GROUPS = ("sync-backup", "updater", "vm", "network", "keep-awake", "other")


def group(category):
    return "sync-backup" if category in {"sync", "backup"} else category if category in GROUPS else "other"


@contextmanager
def read_budget(seconds=15, clock=time.monotonic):
    previous = shell.get_runner()
    deadline = clock() + seconds
    cache = {}

    def remaining():
        value = deadline - clock()
        if value <= 0:
            raise shell.ReadError(t("onboarding.deadline"))
        return value

    def read(command, timeout=None, interactive=False):
        left = remaining()
        if interactive or command[:1] == ["sudo"] or not shell._dry_run_readonly(command):
            raise PermissionError(t("onboarding.readonly", command=" ".join(command)))
        key = tuple(command)
        if key not in cache:
            cache[key] = previous(command, min(timeout or 5, left), False)
        return cache[key]

    shell.set_runner(read)
    try:
        yield remaining
    finally:
        shell.set_runner(previous)


def scan(*, seconds=15, clock=time.monotonic):
    """Read only, fail before proposing any write when a required scan fails."""
    from .failures import capture

    config = load_config()
    with capture() as commands:
        try:
            with read_budget(seconds, clock) as remaining, snapshot_scope() as snapshot:
                apps = app_policy.discover()
                apps = [row for row in apps if row["name"] not in config.apps.ignore]
                for row in apps:
                    remaining()
                    rule = categories.identify(name=row["name"], bundle_id=row.get("bundle_id", ""))
                    row["group"] = group(rule["category"] if rule else None)
                    row["rule_id"] = rule["id"] if rule else None
                    row["configured"] = any(config.apps.explicit(row["name"], s) for s in ("work", "away"))
                services = launchd.svc_scan(config)
                errors = [row["error"] for row in services["invalid"]]
                errors.extend(row["cpu_error"] for row in services["recognized"] if row.get("cpu_error"))
                if errors:
                    raise shell.ReadError(t("onboarding.scan_invalid", reason="; ".join(errors)))
                agents = []
                for row in services["recognized"]:
                    remaining()
                    rule = categories.identify(label=row["label"], executable=row.get("executable", ""))
                    if row["label"].startswith("com.apple.") or (rule and rule["action"] == "notice"):
                        continue
                    if rule and (categories.ignored(rule, config) or categories.protected(rule, config)):
                        continue
                    if not rule and (not row["cpu"] or float(row["cpu"]) < config.health.cpu_threshold):
                        continue
                    keep = any(launchd.match_services(s, [row]) for s in config.services.keep)
                    explicit = keep or any(launchd.match_services(s, [row]) for s in config.services.unload)
                    agents.append(
                        dict(
                            row,
                            group=group(rule["category"] if rule else None),
                            recommendation="keep" if keep else "unload",
                            configured=explicit,
                        )
                    )
                raw = shell._read(["pmset", "-g"], timeout=5)
                holders = pmset.sleep_blocking_assertions(pmset.pmset_assertions())
                readiness = sleep_readiness.evaluate(
                    pmset.parse_sleep_disabled(raw), holders, processes=snapshot.processes
                )
                remaining()
                return dict(apps=apps, services=agents, readiness=readiness)
        except Exception as exc:
            exc.setup_commands = commands
            raise


def choices(data, config=None):
    config = config or load_config()
    selected = app_policy.choices(data["apps"], config)
    selected["services"] = {row["label"]: row["recommendation"] for row in data["services"]}
    selected["network"] = [row["name"] for row in data["apps"] if row["group"] == "network"]
    return selected


def detected(data):
    counts = {key: 0 for key in GROUPS}
    for row in (*data["apps"], *data["services"]):
        counts[row["group"]] += 1
    order = ("sync-backup", "updater", "network", "other", "vm", "keep-awake")
    items = [
        t("onboarding.detected_item", name=t("onboarding.group_" + key), n=counts[key])
        for key in order
        if counts[key]
    ]
    return t("onboarding.detected", items=" · ".join(items)) if items else t("onboarding.none")


def display_path(path):
    path = Path(path)
    try:
        relative = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    return str(Path("~") / relative)


def summary(selected, path):
    return t(
        "onboarding.summary",
        path=display_path(path),
        work_scenario=t("scenario.work"),
        away_scenario=t("scenario.away"),
        wf=sum(v == "freeze" for v in selected["work"].values()),
        wq=sum(v == "quit" for v in selected["work"].values()),
        af=sum(v == "freeze" for v in selected["away"].values()),
        aq=sum(v == "quit" for v in selected["away"].values()),
        services=sum(v == "unload" for v in selected.get("services", {}).values()),
        network=sum(
            all(selected[s].get(n) == "skip" for s in ("work", "away")) for n in selected.get("network", [])
        ),
    )


def reason(row):
    text = (
        app_policy.explanation(row)
        if "reason" in row
        else t("onboarding.service_reason_" + row.get("category", row["group"]))
    )
    return t("onboarding.your_setting", reason=text) if row.get("configured") else text
