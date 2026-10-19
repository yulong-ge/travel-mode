"""System adapter: pmset."""

from __future__ import annotations

import re
import shlex

from ..config import load_config
from ..i18n import t
from .shell import ReadError, _read

POWER_KEYS = [
    ("lowpowermode", t("impl.low_power_mode_reduce_energy"), "1"),
    ("sleep", t("impl.sleep_after_n_idle_minutes_disables"), "5"),
    ("displaysleep", t("impl.turn_off_the_display_after_n"), "3"),
    ("powernap", t("impl.power_nap_wake_periodically_for_mail"), "0"),
    ("tcpkeepalive", t("impl.keep_tcp_connections_during_sleep_for"), "1"),
    ("womp", t("impl.wake_on_network_packets_while_the"), "0"),
    ("standby", t("impl.deep_standby_write_memory_to_disk"), "1"),
    ("hibernatemode", t("impl.hibernate_ram_ram_disk_disk_only"), "3"),
    ("ttyskeepawake", t("impl.prevent_sleep_while_a_terminal_is"), "1"),
    ("disksleep", t("impl.put_disks_to_sleep_after_n"), "10"),
    ("lessbright", t("impl.reduce_display_brightness_on_battery"), "1"),
    ("networkoversleep", t("impl.network_availability_during_sleep"), "0"),
]
POWER_KEY_ORDER = [k for k, _, _ in POWER_KEYS]


def sleep_blocking_assertions(lines=None, daemon_apps=()):
    """Parse process holders of system sleep blocking assertions."""
    if lines is None:
        lines = pmset_assertions()
    daemon_names = {item.name for item in daemon_apps}
    result = []
    for line in lines:
        if not re.search(r"Prevent(?:UserIdle)?SystemSleep", line):
            continue
        pid_match = re.search(r"(?:pid\s+)?(\d+)\s*\(([^)]+)\)", line)
        named_match = re.search(r"named:\s*\"?([^\"]+)\"?", line)
        process = pid_match.group(2).strip() if pid_match else ""
        named = named_match.group(1).strip() if named_match else ""
        owner = named or process
        duration = re.search(r"\b(\d+):(\d{2}):(\d{2})\b", line)
        held = (
            sum(int(v) * scale for v, scale in zip(duration.groups(), (3600, 60, 1), strict=True))
            if duration
            else None
        )
        result.append(
            {
                "pid": pid_match.group(1) if pid_match else "",
                "process": process,
                "named": named,
                "type": "PreventSystemSleep"
                if "PreventSystemSleep" in line
                else "PreventUserIdleSystemSleep",
                "daemon_app": owner in daemon_names
                or any(name.lower() in line.lower() for name in daemon_names),
                "line": line.strip(),
                "scope": "system" if "PreventSystemSleep" in line else "idle",
                "held_seconds": held,
            }
        )
    return result


def pmset_custom():
    """pmset -g custom -> {"a": {...}, "b": {...}, "c": {...}}"""
    return parse_custom(_read(["pmset", "-g", "custom"]))


def parse_custom(out):
    profs, cur = {}, None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("AC Power"):
            cur = "a"
        elif s.startswith("Battery Power"):
            cur = "b"
        elif s.startswith("UPS Power"):
            cur = "c"
        elif cur:
            parts = s.split()
            if parts and parts[0] in {*POWER_KEY_ORDER, "powermode"}:
                if len(parts) != 2 or not re.fullmatch(r"\d+", parts[1]):
                    raise ReadError(
                        t("power.invalid_field", command="pmset -g custom", field=parts[0], value=s)
                    )
                if parts[0] in profs.get(cur, {}):
                    raise ReadError(
                        t("power.invalid_field", command="pmset -g custom", field=parts[0], value=s)
                    )
            if len(parts) >= 2:
                profs.setdefault(cur, {})[parts[0]] = parts[1]
        if cur:
            profs.setdefault(cur, {})
    if not profs:
        raise ReadError(t("safety.missing_power_profiles"))
    return profs


def pmget(key, profile, profs=None):
    return (profs or pmset_custom()).get(profile, {}).get(key)


def pmset_g():
    """pmset -g (当前生效) -> dict, 含 SleepDisabled."""
    out = _read(["pmset", "-g"])
    d = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[A-Za-z]+", parts[0]):
            d[parts[0]] = parts[1]
    return d


def sleep_disabled():
    return parse_sleep_disabled(_read(["pmset", "-g"]))


def parse_optional_setting(output, key, allowed):
    if not output.strip():
        raise ReadError(t("power.empty_output", command="pmset -g"))
    rows = [line.split() for line in output.splitlines() if line.split()[:1] == [key]]
    if not rows:
        known = {*POWER_KEY_ORDER, "powermode", "SleepDisabled"}
        if not any(
            line.strip() in {"System-wide power settings:", "Currently in use:"}
            or bool(line.split() and line.split()[0] in known)
            for line in output.splitlines()
        ):
            raise ReadError(t("power.invalid_field", command="pmset -g", field=key, value=output))
        return None
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] not in allowed:
        raise ReadError(t("power.invalid_field", command="pmset -g", field=key, value=repr(rows)))
    return rows[0][1]


def parse_sleep_disabled(output):
    """Absence is an unsupported capability; a present malformed value is an error."""
    return parse_optional_setting(output, "SleepDisabled", {"0", "1"})


def pmset_assertions():
    out = _read(["pmset", "-g", "assertions"])
    return out.splitlines()


def configured_sleep_blocking_assertions():
    from ..system.pmset import sleep_blocking_assertions as parse_assertions

    return parse_assertions(pmset_assertions(), load_config().apps.with_daemon)


def sleep_blocking_assertion():
    """Compatibility helper for callers from phase 1."""
    rows = configured_sleep_blocking_assertions()
    return rows[0]["line"] if rows else ""


def audio_assertion():
    for line in pmset_assertions():
        if "audio" in line and "PreventUserIdle" in line:
            return line.strip()
    return ""


def battery_line():
    out = _read(["pmset", "-g", "batt"])
    for line in out.splitlines():
        if "InternalBattery" in line:
            return re.sub(r"\t+", " ", line).strip()
    return ""


def on_ac_power():
    out = _read(["pmset", "-g", "ps"])
    if "AC Power" in out:
        return True
    if "Battery Power" in out or "UPS Power" in out:
        return False
    raise ReadError(t("power.invalid_field", command="pmset -g ps", field="power source", value=out))


def _profile_args(profile: str) -> list[str]:
    """Filter a saved profile against this Mac's advertised pmset keys."""
    from ..system.platform import map_pmset_key, supported_pmset_keys

    supported = supported_pmset_keys()
    tokens = shlex.split(profile)
    result: list[str] = []
    if len(tokens) % 2:
        raise ValueError(t("safety.invalid_power_profile", profile=profile))
    for index in range(0, len(tokens), 2):
        actual = map_pmset_key(tokens[index], supported)
        if actual is None:
            return []
        result.extend((actual, tokens[index + 1]))
    return result


def power_recommendations(profiles=None):
    from ..i18n import t
    from ..system.platform import filter_power_keys, supported_pmset_keys

    overrides = load_config().power_overrides
    descriptions = {
        "lowpowermode": t("constants.low_power_mode"),
        "sleep": t("constants.idle_minutes_before_system_sleep"),
        "displaysleep": t("constants.minutes_before_display_sleep"),
        "tcpkeepalive": t("constants.keep_tcp_connections_during_sleep"),
        "womp": t("constants.wake_on_network_access"),
        "standby": t("constants.deep_standby"),
        "hibernatemode": t("constants.hibernation_mode"),
        "ttyskeepawake": t("impl.prevent_sleep_while_a_terminal_is"),
        "disksleep": t("constants.minutes_before_disk_sleep"),
        "lessbright": t("constants.dim_display_on_battery"),
        "networkoversleep": t("constants.network_during_sleep"),
        "powernap": "Power Nap",
    }
    recommendations = [(key, desc, overrides.get(key, rec)) for key, desc, rec in POWER_KEYS]
    if load_config().options.scenario == "work":
        recommendations = [row for row in recommendations if row[0] == "lowpowermode"]
    current = (pmset_custom() if profiles is None else profiles).get("b", {})
    if not current:
        return []
    supported = supported_pmset_keys() & current.keys()
    return [
        (key, descriptions.get(key, desc), rec)
        for key, desc, rec in filter_power_keys(recommendations, supported)
    ]


def power_preview(profiles, *, scenario=None, supported=None, config=None):
    """A content model: actual changes, satisfied names and unavailable names."""
    from .platform import map_pmset_key, supported_pmset_keys

    config = config or load_config()
    scenario = scenario or config.options.scenario
    supported = supported_pmset_keys() if supported is None else supported
    result = dict(changes=[], satisfied=[], unavailable=[], ac_changes=[])
    for key, _, default in POWER_KEYS:
        if scenario == "work" and key != "lowpowermode":
            continue
        actual = map_pmset_key(key, supported & profiles.get("b", {}).keys())
        name = t("preview.name." + key)
        current = profiles.get("b", {}).get(actual)
        target = config.power_overrides.get(key, default)
        if current is None:
            result["unavailable"].append(name)
        elif current == target:
            result["satisfied"].append(name)
        else:
            result["changes"].append(
                dict(key=actual, name=name, current=current, target=target, explanation=t("preview." + key))
            )
    for key in ("powernap", "womp"):
        current = profiles.get("a", {}).get(key)
        if current is not None and current != "0":
            result["ac_changes"].append(
                dict(key=key, current=current, target="0", explanation=t("preview.ac_" + key))
            )
    return result
