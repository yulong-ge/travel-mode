"""One finite application snapshot supplies recommendations and setup choices."""

from __future__ import annotations

import re
from pathlib import Path

from rich.cells import cell_len

from .i18n import t

# Interactive tools remain opt-in. Match public bundle identities as well as names.
INTERACTIVE_NAMES = {
    "Google Chrome",
    "Microsoft Edge",
    "Firefox",
    "Brave Browser",
    "Arc",
    "Safari",
    "Visual Studio Code",
    "Cursor",
    "Zed",
    "Xcode",
    "TextEdit",
    "Obsidian",
    "Terminal",
    "iTerm",
    "iTerm2",
    "Warp",
    "Alacritty",
    "kitty",
    "Ghostty",
}
INTERACTIVE_IDS = {
    "com.google.Chrome",
    "com.microsoft.edgemac",
    "org.mozilla.firefox",
    "com.brave.Browser",
    "company.thebrowser.Browser",
    "com.apple.Safari",
    "com.microsoft.VSCode",
    "com.todesktop.230313mzl4w4u92",
    "dev.zed.Zed",
    "com.apple.dt.Xcode",
    "com.sublimetext.4",
    "com.apple.TextEdit",
    "md.obsidian",
    "com.apple.Terminal",
    "com.googlecode.iterm2",
    "dev.warp.Warp-Stable",
    "org.alacritty",
    "net.kovidgoyal.kitty",
    "com.mitchellh.ghostty",
}


def recommend(name, *, rule=None, bundle_id="", assertion=False):
    if rule and rule.get("file_provider"):
        return dict(work="quit", away="quit", reason="provider")
    if assertion:
        return dict(work="freeze", away="quit", reason="assertion")
    if name in INTERACTIVE_NAMES or bundle_id in INTERACTIVE_IDS:
        return dict(work="ask", away="ask", reason="interactive")
    if rule and rule["action"] == "notice":
        return dict(work="skip", away="skip", reason="notice")
    return dict(work="freeze", away="freeze", reason="freeze")


def foreground_pid():
    from .system.shell import ReadError, _read

    front = _read(["lsappinfo", "front"], timeout=5).strip()
    if not re.fullmatch(r"ASN:0x[0-9a-fA-F]+-0x[0-9a-fA-F]+:", front):
        raise ReadError(t("setup.foreground_error", detail=repr(front)))
    info = _read(["lsappinfo", "info", "-only", "pid,bundleid", front], timeout=5)
    match = re.search(r"\bpid\s*=\s*(\d+)\b", info)
    if not match:
        raise ReadError(t("setup.foreground_error", detail=repr(info)))
    return match[1]


def recommendations(snapshot):
    from .categories import identify
    from .system.apps import app_bid
    from .system.pmset import pmset_assertions, sleep_blocking_assertions
    from .system.shell import ReadError

    lines = pmset_assertions()
    if not any("Assertion status system-wide:" in line for line in lines):
        raise ReadError(t("setup.assertions_error"))
    holders = sleep_blocking_assertions(lines)
    foreground = foreground_pid()
    rows = []
    for path in snapshot.index.paths:
        # System bundles are never candidates for personalized mutation defaults.
        if path.startswith("/System/"):
            continue
        name = Path(path).stem
        bid = app_bid(path)
        processes = snapshot.matching("^" + re.escape(path) + "/")
        pids = {p.pid for p in processes}
        assertion = any(
            h["pid"] in pids or h["process"].casefold() == name.casefold() for h in holders if h["pid"]
        )
        rule = identify(name=name, bundle_id=bid)
        recommendation = recommend(name, rule=rule, bundle_id=bid, assertion=assertion)
        if foreground in pids:
            recommendation = dict(work="skip", away="skip", reason="foreground")
        rows.append(
            dict(
                name=name,
                path=path,
                work_scenario=t("scenario.work"),
                away_scenario=t("scenario.away"),
                bundle_id=bid,
                running=bool(processes),
                **recommendation,
            )
        )
    return sorted(rows, key=lambda row: (row["reason"], row["name"].casefold()))


def setup_candidates(rows, processes):
    """Omit protected apps from both setup clients without changing saved preferences."""
    from .system.process_control import _prefixes, protected_app_identity, protected_process_ids

    protected = protected_process_ids(processes)
    return [
        row
        for row in rows
        if row["reason"] != "foreground"
        and not protected_app_identity(row["path"], row.get("bundle_id", ""))
        and not any(
            process.pid in protected and process.command.startswith(_prefixes(row["path"], True))
            for process in processes
        )
    ]


def discover():
    from .failures import capture
    from .system.app_snapshot import snapshot_scope
    from .system.process_control import process_table

    with capture() as commands:
        try:
            with snapshot_scope() as snapshot:
                return setup_candidates(recommendations(snapshot), process_table())
        except Exception as exc:
            exc.setup_commands = commands
            raise


def choices(rows, config):
    return {
        scenario: {
            row["name"]: (
                "skip"
                if row["name"] in config.apps.ignore or row["reason"] == "foreground"
                else config.apps.explicit(row["name"], scenario) or row[scenario]
            )
            for row in rows
        }
        for scenario in ("work", "away")
    }


def row_text(row, selected):
    name = t("setup.wechat") if row["name"] == "WeChat" else row["name"]
    name += " " * max(0, 18 - cell_len(name))
    return t(
        "setup.row",
        name=name,
        work_scenario=t("scenario.work"),
        away_scenario=t("scenario.away"),
        work=t("action." + selected["work"][row["name"]]),
        away=t("action." + selected["away"][row["name"]]),
        reason=t("setup.reason_" + row["reason"]),
    )


def summary(selected, path):
    return t(
        "setup.summary",
        path=path,
        work_scenario=t("scenario.work"),
        away_scenario=t("scenario.away"),
        wf=sum(v == "freeze" for v in selected["work"].values()),
        wq=sum(v == "quit" for v in selected["work"].values()),
        af=sum(v == "freeze" for v in selected["away"].values()),
        aq=sum(v == "quit" for v in selected["away"].values()),
    )
