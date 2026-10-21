"""One ordered plan; filtering never changes selection or repeats discovery."""

from __future__ import annotations

from hashlib import sha256

from travel_mode.i18n import t
from travel_mode.tui.view_models import plan_item as item


def collect(scenario, state):
    from travel_mode.config import load_config
    from travel_mode.health_view import presentation
    from travel_mode.network_apps import network_scan
    from travel_mode.profiles import active_values
    from travel_mode.sleep_readiness import cached, evaluate
    from travel_mode.system import apps, launchd, network, pmset
    from travel_mode.system.app_snapshot import snapshot_scope
    from travel_mode.system.health import collect as health_collect
    from travel_mode.system.platform import supported_pmset_keys
    from travel_mode.system.process_control import protected_app_identity

    config = load_config()
    profile_values = active_values()
    with snapshot_scope() as snapshot:
        scan = apps.scan_apps()
        net = network_scan()
        processes = snapshot.processes
        configured_names = (
            set(config.apps.configured_actions)
            | set(config.apps.watch)
            | set(getattr(config.apps, scenario, {}))
        )
        unmatched_apps = [name for name in sorted(configured_names) if not snapshot.index.find(name)]
    svc = launchd.svc_scan()
    profiles = pmset.pmset_custom()
    supported = supported_pmset_keys()
    preview = pmset.power_preview(profiles, scenario=scenario, supported=supported, config=config)
    health = health_collect()
    health_rows = presentation(health, scenario=scenario)
    sleep_row = next(row for row in health["checks"] if row["id"] == "sleep_disabled")
    sd = (
        None
        if sleep_row["status"] in {"unknown", "unavailable"}
        else "1"
        if sleep_row["status"] == "hit"
        else "0"
    )
    assertion_row = next(row for row in health["checks"] if row["id"] == "assertions")
    holders = pmset.sleep_blocking_assertions(assertion_row["evidence"])
    awake = next(row for row in health["checks"] if row["id"] == "keep_awake")
    readiness = evaluate(
        sd,
        holders,
        processes=processes,
        error=sleep_row["error"] or assertion_row["error"],
        caffeinate=awake.get("subjects", ()),
        caffeinate_actions=[a for a in awake["actions"] if a["kind"] == "caffeinate"],
        previous=cached(),
    )
    readiness_pids = {
        member["pid"] for row in readiness["items"] for member in row.get("caffeinate", {}).get("members", [])
    }
    rows, satisfied = [], {key: [] for key in ("power", "apps", "services", "network")}
    # Scoped structured values survive language changes without a new system read.
    satisfied["power"] = preview["satisfied_settings"]
    for domain, changes in (("-b", preview["changes"]), ("-c", preview["ac_changes"])):
        for change in changes:
            key = change["key"]
            if key == "tcpkeepalive":
                # Fixed policy, never selectable; enable it if it was previously off.
                continue
            from .view_models import power_name

            name = power_name(dict(values=[key, change["target"]], domain=domain))
            rows.append(
                item(
                    f"power-{domain[1:]}-{key}",
                    "power",
                    name,
                    "apply",
                    change["explanation"],
                    dict(kind="power", values=[key, change["target"]], domain=domain),
                    evidence=[str(change)],
                )
            )
    for domain, profile in (("-b", profiles.get("b", {})), ("-c", profiles.get("a", {}))):
        if profile.get("tcpkeepalive") not in {None, "1"}:
            rows.append(
                item(
                    f"keepalive-{domain[1:]}",
                    "power",
                    t("preview.name.tcpkeepalive"),
                    "apply",
                    t("redesign.keepalive"),
                    dict(kind="power", values=["tcpkeepalive", "1"], domain=domain),
                )
            )
            rows[-1].update(fixed=True, operations=[])
    if scenario == "away" and "hibernatemode" in profiles.get("b", {}) and "hibernatemode" in supported:
        rows.append(
            item(
                "deep-sleep",
                "power",
                t("redesign.hibernation"),
                "apply",
                t("redesign.hibernation_reason"),
                dict(kind="power", values=["hibernatemode", "25"], domain="-b"),
                selected=profile_values.get("power", {}).get("battery", {}).get("hibernatemode") == 25,
            )
        )
    for app in scan["running"]:
        if app["defact"] == "skip" or protected_app_identity(app["path"], app.get("bundle_id", "")):
            satisfied["apps"].append(app["name"])
            continue
        reason = t("setup.reason_" + app.get("reason", "freeze"))
        row = item(
            "app-" + sha256(app["path"].encode()).hexdigest()[:10],
            "apps",
            app["name"],
            app["defact"],
            reason,
            dict(kind="app", name=app["name"], path=app["path"]),
            selected=app["defact"] in {"quit", "freeze"},
            evidence=[app["path"], str(app.get("cpu", ""))],
        )
        if app["path"] in state.get("frozen_apps", ()) or app["path"] in state.get("quit_apps", ()):
            row.update(
                state="done",
                selected=False,
                operations=[],
                app_running=False,
                result=t(
                    "app_view.frozen" if app["path"] in state.get("frozen_apps", ()) else "app_view.exited"
                ),
            )
        rows.append(row)
    app_paths = {r["descriptor"]["path"] for r in rows if r["group"] == "apps"}
    app_paths.update(
        app["path"]
        for app in scan["running"]
        if config.apps.explicit(app["name"], scenario) == "skip"
        or app.get("reason") == "foreground"
        or app.get("category_ignored")
    )
    for app in net:
        if (
            not app["running"]
            or not app["path"]
            or app["path"] in app_paths
            or protected_app_identity(app["path"])
            or app["name"] in config.apps.ignore
        ):
            continue
        rows.append(
            item(
                "network-app-" + sha256(app["path"].encode()).hexdigest()[:10],
                "network",
                app["name"],
                "quit",
                t("redesign.network_reason"),
                dict(kind="app", name=app["name"], path=app["path"]),
                selected=False,
                evidence=[str(app)],
            )
        )
        app_paths.add(app["path"])
    for service in svc["unloadable"]:
        path = service["plist_path"]
        if path in state.get("unloaded_svcs", ()):
            satisfied["services"].append(service.get("name", service["label"]))
            continue
        rows.append(
            item(
                "service-" + sha256(path.encode()).hexdigest()[:10],
                "services",
                service.get("name", service["label"]),
                "pause",
                t("redesign.service_reason"),
                dict(kind="agent", path=path),
                evidence=[str(service)],
            )
        )
    dev = network.wifi_dev()
    if dev and network.wifi_powered(dev):
        rows.append(
            item(
                "wifi",
                "network",
                "Wi-Fi",
                "off_action",
                t("redesign.findmy_warning"),
                dict(kind="wifi", device=dev),
                selected=False,
            )
        )
    from travel_mode.system.shell import command_path

    if command_path("blueutil") and network.bt_powered():
        rows.append(
            item(
                "bluetooth",
                "network",
                "Bluetooth",
                "off_action",
                t("redesign.findmy_warning"),
                dict(kind="bluetooth"),
                selected=False,
            )
        )
    spotlight_row = next(row for row in health["checks"] if row["id"] == "spotlight")
    if any("Indexing enabled" in evidence for evidence in spotlight_row["evidence"]):
        rows.append(
            item(
                "spotlight",
                "services",
                "Spotlight",
                "pause",
                t("redesign.spotlight_reason"),
                dict(kind="spotlight"),
                selected=profile_values.get("spotlight") == "off",
            )
        )
    brightness_row = next(row for row in health["checks"] if row["id"] == "brightness")
    if brightness_row["status"] in {"clear", "hit"} and any(
        "brightness=" in evidence for evidence in brightness_row["evidence"]
    ):
        rows.append(
            item(
                "brightness",
                "power",
                t("redesign.brightness"),
                "apply",
                t("redesign.brightness_reason"),
                dict(
                    kind="brightness", value=profile_values.get("brightness", config.health.brightness_target)
                ),
                selected=profile_values.get("brightness") is not None,
            )
        )
    # Remediations use the same Action factory; do not duplicate an app or setting.
    health_actions = []
    normal = 0
    for finding in health_rows:
        if finding["id"] in {"sleep_disabled", "assertions"}:
            continue
        if finding["id"] == "keep_awake" and readiness_pids:
            # The readiness line owns both the explanation and exact-PID button.
            import re

            remaining = dict(awake)
            remaining["actions"] = [
                a
                for a in awake["actions"]
                if a["kind"] != "caffeinate" or a["process"]["pid"] not in readiness_pids
            ]
            remaining["subjects"] = [s for s in awake.get("subjects", ()) if s["pid"] not in readiness_pids]
            remaining["evidence"] = [
                line
                for line in awake["evidence"]
                if not any(int(pid) in readiness_pids for pid in re.findall(r"\bPID\s+(\d+)", line))
            ]
            if not remaining["evidence"]:
                continue
            finding = presentation({**health, "checks": [remaining]}, scenario=scenario)[0]
        if finding["state"] == "normal":
            normal += 1
            continue
        descriptors = []
        for descriptor in finding["actions"]:
            if descriptor.get("path") in app_paths:
                continue
            if descriptor["kind"] == "low_power" and any(
                r["descriptor"].get("values", [None])[0] in {"lowpowermode", "powermode"} for r in rows
            ):
                continue
            descriptors.append(descriptor)
        health_actions.append(dict(**finding, available_actions=descriptors))
    for index, finding in enumerate(readiness["items"]):
        if finding["action"]:
            finding["button_id"] = f"ready-{index}"
        elif finding.get("holder"):
            holder = finding["holder"]
            app = next(
                (a for a in scan["running"] if a["name"].casefold() == holder["process"].casefold()), None
            )
            if (
                app
                and config.apps.explicit(app["name"], scenario) != "skip"
                and app["name"] not in config.apps.ignore
                and app.get("reason") != "foreground"
                and not app.get("category_ignored")
                and not protected_app_identity(app["path"], app.get("bundle_id", ""))
            ):
                finding["action"] = dict(kind="quit", name=app["name"], path=app["path"])
                finding["button_id"] = f"ready-{index}"
    noise = dict(
        apps=scan["nobundle"] + unmatched_apps,
        services=svc.get("invalid", []),
        network=[r for r in net if not r["path"]],
        selectors=svc.get("unmatched", []),
    )
    for row in rows:
        kind = row["descriptor"]["kind"]
        if kind == "power":
            key = row["descriptor"]["values"][0]
            translated = "lowpowermode" if key == "powermode" else key
            if row["id"] == "deep-sleep":
                row.update(name_key="redesign.hibernation", reason_key="redesign.hibernation_reason")
            else:
                row["name_key"] = "preview.name." + translated
                if row["descriptor"]["domain"] == "-c":
                    row["name_key"] = "redesign.ac_" + translated
                row["reason_key"] = "redesign.effect_" + translated
        elif kind == "app":
            app = next((a for a in scan["running"] if a["path"] == row["descriptor"]["path"]), None)
            row["category"] = (app or {}).get("category", "other")
            row["detail_fields"] = dict(path=row["descriptor"]["path"])
            if app and app.get("cpu") is not None:
                row["detail_fields"]["cpu"] = app["cpu"]
            if app:
                row["reason_key"] = (
                    "redesign.quit_reason"
                    if row["action"] == "quit"
                    else "redesign.work_assertion"
                    if scenario == "work" and app.get("reason") == "assertion"
                    else "setup.reason_" + app.get("reason", "freeze")
                )
        elif kind == "agent":
            service = next(s for s in svc["unloadable"] if s["plist_path"] == row["descriptor"]["path"])
            row["category"] = service.get("category", "other")
            row["reason_key"] = "redesign.service_reason"
        elif kind in {"wifi", "bluetooth"}:
            row["reason_key"] = "redesign.findmy_warning"
        elif kind == "spotlight":
            row["reason_key"] = "redesign.spotlight_reason"
        elif kind == "brightness":
            row.update(name_key="redesign.brightness", reason_key="redesign.brightness_reason")
    return dict(
        rows=rows,
        satisfied=satisfied,
        readiness=readiness,
        health=health,
        attention=health_actions,
        normal=normal,
        noise=noise,
        scan=scan,
        services=svc,
    )


def operation(row, scenario, state, save):
    from travel_mode.actions.apps import (
        ForceQuitApp,
        FreezeApp,
        QuitApp,
        QuitDaemonApp,
        ThawApp,
        UnloadAgent,
        configured_daemon,
    )
    from travel_mode.actions.network import BluetoothOff, WifiOff
    from travel_mode.actions.power import SetPowerProfile
    from travel_mode.flows.doctor import make_action

    descriptor = row["descriptor"]
    kind = descriptor["kind"]
    if kind == "app":
        path, name = descriptor["path"], descriptor["name"]
        if row["action"] == "freeze":
            return FreezeApp(path)
        if row["action"] == "thaw":
            return ThawApp(path, state)
        if row["action"] == "force":
            return ForceQuitApp(path)
        spec = configured_daemon(name)
        return QuitDaemonApp(spec, state, save) if spec else QuitApp(path, name, fallback=scenario == "away")
    if kind == "agent":
        return UnloadAgent(descriptor["path"])
    if kind == "power":
        return SetPowerProfile(descriptor["values"], descriptor["domain"])
    if kind == "wifi":
        return WifiOff(descriptor["device"])
    if kind == "bluetooth":
        return BluetoothOff()
    return make_action(descriptor, state, save)


def success(result):
    if isinstance(result, tuple):
        return bool(result[1]) if len(result) == 3 else result[1] == 0
    return bool(result)


def execution_units(rows, scenario, state, save):
    """Each power domain is one atomic Action, with results shared by its rows."""
    from travel_mode.actions.power import SetPowerProfile

    rows = [
        r for r in rows if r["selected"] and r["state"] == "planned" and r["action"] not in {"ask", "skip"}
    ]
    emitted = set()
    for row in rows:
        descriptor = row["descriptor"]
        if descriptor["kind"] != "power":
            yield [row], operation(row, scenario, state, save)
            continue
        domain = descriptor["domain"]
        if domain in emitted:
            continue
        emitted.add(domain)
        unit = [r for r in rows if r["descriptor"]["kind"] == "power" and r["descriptor"]["domain"] == domain]
        values = {}
        for member in unit:
            key, value = member["descriptor"]["values"]
            # The explicit deeper-sleep option overrides the normal sleep recommendation.
            if key in values and values[key] != value and member["id"] != "deep-sleep":
                continue
            values[key] = value
        yield unit, SetPowerProfile([token for pair in values.items() for token in pair], domain, len(unit))


def covers(row, descriptor):
    planned = row["descriptor"]
    kind = descriptor["kind"]
    if kind == "low_power":
        return (
            planned["kind"] == "power"
            and planned["domain"] == "-b"
            and planned["values"][0] in {"lowpowermode", "powermode"}
            and planned["values"][1] == "1"
        )
    if kind == "quit":
        return (
            planned["kind"] == "app"
            and planned["path"] == descriptor["path"]
            and row["action"] in {"quit", "force"}
        )
    if kind == "freeze":
        return (
            planned["kind"] == "app"
            and planned["path"] == descriptor["path"]
            and row["action"] in {"quit", "force", "freeze"}
        )
    if kind == "brightness":
        return planned["kind"] == kind and planned["value"] <= descriptor["value"]
    return kind == planned["kind"] and kind in {"spotlight", "sleep", "caffeinate"} and planned == descriptor


def finding_covered(finding, rows, *, session=False):
    descriptors = finding.get("actions", ())
    return bool(descriptors) and all(
        any(
            covers(row, descriptor)
            and (
                row["state"] in {"done", "warning"}
                or (not session and row["state"] == "planned" and row["selected"])
            )
            for row in rows
        )
        for descriptor in descriptors
    )
