"""Operational sudo command catalog, shared by path resolution and sudoers generation.

Administrative sudoers installation/removal is deliberately not passwordless.
Argument globs are sudoers globs, not application-level PID/plist protections.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Privilege:
    tool: str
    path: str
    arguments: str | None
    risk: str
    probe: tuple[str, ...]


PRIVILEGES = (
    Privilege("pmset", "/usr/bin/pmset", None, "sudoers.risk_pmset", ("-b", "lowpowermode", "1")),
    Privilege("launchctl", "/bin/launchctl", "list", "sudoers.risk_list", ("list",)),
    Privilege("launchctl", "/bin/launchctl", "load *", "sudoers.risk_load", ("load", "/example.plist")),
    Privilege("launchctl", "/bin/launchctl", "unload *", "sudoers.risk_unload", ("unload", "/example.plist")),
    Privilege("mdutil", "/usr/bin/mdutil", "-i on /", "sudoers.risk_index", ("-i", "on", "/")),
    Privilege("mdutil", "/usr/bin/mdutil", "-i off /", "sudoers.risk_index", ("-i", "off", "/")),
    Privilege(
        "networksetup",
        "/usr/sbin/networksetup",
        "-setairportpower * on",
        "sudoers.risk_wifi",
        ("-setairportpower", "en0", "on"),
    ),
    Privilege(
        "networksetup",
        "/usr/sbin/networksetup",
        "-setairportpower * off",
        "sudoers.risk_wifi",
        ("-setairportpower", "en0", "off"),
    ),
    Privilege("kill", "/bin/kill", "-TERM *", "sudoers.risk_kill", ("-TERM", "999999")),
)
PRIVILEGED_PATHS = {rule.tool: rule.path for rule in PRIVILEGES}


def rules(scope):
    if scope not in {"pmset", "all"}:
        raise ValueError(f"sudoers scope: {scope!r}")
    return tuple(rule for rule in PRIVILEGES if scope == "all" or rule.tool == "pmset")
