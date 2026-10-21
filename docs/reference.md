# travel-mode technical reference

Detailed behavior behind the [README](../README.md). Statements here describe the current code;
hardware observations name the machine they were made on.

- [Scenarios and profiles](#scenarios-and-profiles)
- [Categories and precedence](#categories-and-precedence)
- [Prompts, `--yes` and interfaces](#prompts---yes-and-interfaces)
- [Dry run](#dry-run)
- [Sudo and tool paths](#sudo-and-tool-paths)
- [Health check](#health-check)
- [Power measurement](#power-measurement)
- [Low Power Mode](#low-power-mode)
- [Session reports and history](#session-reports-and-history)
- [Event automation](#event-automation)
- [Menu bar](#menu-bar)
- [Passwordless sudo](#passwordless-sudo)
- [Restore and recovery](#restore-and-recovery)
- [Configuration and environment](#configuration-and-environment)
- [Failed actions and the log](#failed-actions-and-the-log)
- [App defaults and setup](#app-defaults-and-setup)
- [Exit codes and errors](#exit-codes-and-errors)

## Scenarios and profiles

Built-in profiles are `work`, `flight` and `overnight`. `work` belongs to the work scenario;
`flight` and `overnight` belong to away. A custom `[profiles.NAME]` overrides the matching built-in
for the fields it sets and defaults to away unless it sets `scenario = "work"`. An explicit
`--scenario` that conflicts with the profile's scenario is an error. `options.scenario = "ask"`
(the default) shows the scenario picker.

The identifiers stay `work` and `away`; the TUI and messages call them **On the go** and
**Overnight / flight**.

**work (On the go)** enables battery Low Power Mode, turns off Power Nap and wake for network
access (`womp`) on battery and on AC power, selects the configured categories, unloads configured
user LaunchAgents and applies app actions (freeze by default, see "App defaults and setup").
Sleep and display-sleep timers, `hibernatemode`, Wi-Fi, Bluetooth and brightness are unchanged;
brightness is an unselected option and a profile can set a target between 0 and 1. Power Nap and
`womp` only act while the Mac sleeps, so applying them with the lid open does not affect work.
Spotlight indexing is optional.

**away (Overnight / flight)** also shortens the battery sleep and display-sleep timers, quits apps
instead of freezing them, and offers `hibernatemode 25` ("deeper sleep") and Wi-Fi/Bluetooth off
as unselected options with the warning that Find My may not locate the Mac. The `flight` profile
selects deeper sleep, a 2-minute display sleep, Spotlight indexing off and 15% brightness, and
keeps Wi-Fi and Bluetooth on.

Neither scenario turns off `tcpkeepalive`; the plan shows it as a fixed line because Find My needs
it. The plan lists only settings that change; settings already at the target are summarised per
power source ("Already set on battery: …").

A snapshot records the last applied scenario while keeping the first original values for recovery.

## Categories and precedence

[`categories.toml`](../src/travel_mode/categories.toml) defines `sync`, `backup`, `updater`, `vm` and
`keep-awake` rules. Each rule names a public source and matches launchd labels, bundle IDs, exact app
names or executable paths. `suggest`, the Services page and the Apps page group matches by category.

- `categories.pause` selects the categories paused automatically in work; `vm` must be listed
  explicitly. The Pause sync and backups / Pause updaters buttons work in either scenario.
- Category unload applies only to loaded, plist-backed LaunchAgents in `~/Library/LaunchAgents`
  and `/Library/LaunchAgents`. System LaunchDaemons are never unloaded by a category.
- iCloud, Time Machine, some backup products and embedded updaters are identification-only; manage
  them inside the product. Category quit is graceful and never forced.
- Category actions add to `services.unload` and explicit `apps.quit`/`apps.freeze`; an identity is
  processed once per batch.
- `services.keep` wins over unload selectors and categories. `apps.skip` and `apps.ignore` protect
  apps and their matching category agents. Explicit app actions, including `ask`, win over
  category defaults. `categories.ignore` suppresses automatic category actions and buttons;
  explicit service/app settings still apply.
- `apps.with_daemon` is a separate, explicitly configured mechanism for apps whose launchd daemon
  keeps them running.

Service selectors in `services.unload` match, in order: exact label, exact plist file name, glob,
case-insensitive substring, and hyphen-separated words that all appear in the label (so
`nightly-backup` matches `com.example.nightly-cloud-backup`). Selectors that match nothing are
listed by `check`.

## Prompts, `--yes` and interfaces

- `--yes` requires `--scenario`, `--profile` or a configured scenario. It applies safe defaults and
  skips actions marked aggressive unless `--aggressive` is also given.
- In work, `--yes` skips `ask` apps and optional Spotlight; explicit quit/freeze settings still apply.
  Work batches do not force quit; a failed quit stops the batch.
- Away `--yes` accepts AC changes and the force-quit fallback after a failed graceful quit.
- Interface priority: `--tui`/`--no-tui`, then `TRAVEL_MODE_INTERFACE=cli|tui`, then
  `options.interface`. A non-interactive terminal runs `check`, except with explicit `--no-tui` or
  `--yes`.

## Dry run

`--dry-run` records mutation commands instead of running them and copies configuration, legacy
files and state into a temporary directory, so configuration writes, migration and recovery
records stay isolated. Observations still run. In an interactive terminal it may run `sudo -v` so
a privileged read (for example a system daemon's load state) can complete; without credentials
that item is reported as "unable to confirm (requires sudo)" and the command exits non-zero.
Messages describe planned actions ("will quit", "will freeze N processes"). A preview does not
prove that the real run will succeed. Before/after power measurement is skipped.

## Sudo and tool paths

Sudo is requested only for actions that need it: power writes, global sleep repair, system
daemon load/unload, indexing, Wi-Fi and signals to root-owned processes. Permission for the exact
command is first checked with `sudo -n -l`; a password is requested only when needed and only in
an interactive terminal. Privileged reads use `sudo -n` and never wait for a password.

Before a batch (apply, restore, a single fix button) the TUI lists the items that would need a
password and asks: enter password, skip these items, or cancel. "Enter password" suspends the TUI,
prints which items need administrator permission and runs `sudo -v` in the terminal; the TUI then
resumes and the commands themselves run with `sudo -n`. Items without permission (no password,
wrong password, Ctrl-C, or "skip") are recorded as not run with the reason "needs administrator
password"; the other items continue, and the session screen offers "Retry" for each. The
interactive CLI asks for the password once before the batch. With `--yes` and no passwordless
rule, such items are not run and one line explains why; nothing waits for a password.

Apple tools (`pmset`, `ioreg`, `launchctl`, `ps`, `kill`, `osascript`, `sudo` and others) are run
from fixed system paths such as `/usr/bin` and `/usr/sbin`; a same-named program earlier in `PATH`
is never used, and a missing system tool is an error. Optional third-party tools (`blueutil`,
`brightness`) are found on `PATH`; `check` shows the resolved paths (`tool_paths` in JSON).

## Health check

The TUI shows a sleep-readiness block first: ✓ when closing the lid will sleep the Mac, ✗ when
`SleepDisabled` is 1 or an app holds a system-sleep assertion (with the likely app and a repair
button), ▲ for idle-sleep assertions. `status --json` reports it as `sleep_ready`, and the menu bar
shows a warning icon while a ✗ item is present. Findings that a selected plan row already handles
(for example Low Power Mode off when the plan turns it on) are not repeated.

The health section in `check`, `check --json` (`health`) and the TUI overview runs once when the
command or overview opens and on refresh. No timer or background process is involved.

Checks: global `SleepDisabled`; `caffeinate` and known keep-awake apps; non-system holders of
system-sleep assertions; online external displays; Spotlight CPU activity; a running Time Machine
backup; Low Power Mode off on battery; display brightness on battery; background CPU activity.
Each finding has a status, evidence, an impact line and either an action or manual steps.

The default view shows each check as one sentence with one of three marks: ✓ ok, ▲ needs
attention, ? cannot check (with the reason), and at most one action label (for example
"Set to 50%"). PIDs, assertion IDs, executable paths, raw `ps`/`pmset` output and thresholds are
shown only by `check --verbose`, in `check --json` and in the log.

Sleep assertions are reported by scope. `PreventUserIdleSystemSleep` only stops idle sleep: the
Mac stays awake while the lid is open and unused, but closing the lid still sleeps it, so in
`away` it is a note, not a problem. `PreventSystemSleep` and a global `SleepDisabled` can keep the
Mac awake with the lid closed. Some apps change `SleepDisabled` themselves; for example a remote
access app with a "prevent sleep while charging" option can set it to 1 on AC power and back to 0
on battery through its own root helper, and may undo a manual repair. The check reports how long
an assertion has been held; it says an assertion persisted across power changes only when the
tool itself observed both power states.

- Each probe has a 2-second command deadline within a 15-second total budget. A failed probe shows
  "cannot check" with the command and reason; the others continue.
- Spotlight counts only when the combined CPU of `mds`, `mds_stores`, `mdworker` and
  `mdworker_shared` reaches `health.spotlight_cpu_threshold`.
- Background CPU uses one `ps` snapshot (a %CPU estimate, not watts). It excludes other users,
  executables under `/System`, `/usr`, `/bin`, `/sbin` and `/Library/Apple`, this tool and its
  ancestors, stopped processes, common terminals and the foreground app. Up to five processes are
  listed with their category when known.
- Sleep-assertion holders are offered quit, not freeze, because a frozen app keeps its assertion.
  `caffeinate` evidence includes the full command line and parent; termination re-checks the PID's
  identity and assertion first.
- External displays are read through a short-lived CoreGraphics reader; a display ID does not prove
  a dock is attached. Time Machine and displays get manual instructions only.
- Keyboard backlight and Bluetooth wake are not checked: no reliable unprivileged reader was found.
- Some Macs and virtual machines do not report every power field (for example `SleepDisabled` in
  `pmset -g`, or a battery profile). A missing field makes that item **unavailable**
  (`"status": "unavailable"` in JSON) and removes its action; no original value is recorded or
  restored. A field that is present but malformed is still an error.
- JSON keeps the raw `health.checks` and adds `health.display_checks`, where repeated evidence is
  shown once with references.

```toml
[health]
brightness_threshold = 0.7     # fraction, 0 < x <= 1
brightness_target = 0.5        # a profile's brightness target takes precedence
cpu_threshold = 20             # percent
spotlight_cpu_threshold = 20   # percent
```

## Power measurement

`travel-mode power` reads `ioreg -rn AppleSmartBattery` every 5 seconds until it has seen two fresh
telemetry updates (one complete interval) or 180 seconds have passed. On the tested MacBook Pro
(M1 Pro, macOS 27) the registry changed every 20–60 seconds and identical readings in between were
cached data, so readings that have not changed are never counted as new samples. Too few updates
returns **insufficient samples**, without a number. AC power, charging or no battery returns
"not measurable". `--seconds` and `--updates` override the window for one command.

- **Source.** Preferred: `PowerTelemetryData.AccumulatedSystemLoad` and
  `SystemLoadAccumulatorCount`. The mean power of an interval is the change in the accumulator (mW)
  divided by the change in the count; the overall mean weights intervals by their counts. A
  battery-discharge mean is reported too when its accumulator advances. Only when these fields are
  missing is voltage × signed current used, labelled `voltage_current`; on the tested Mac the
  instantaneous current was quantized and less reliable.
- **Validity.** Counter resets or wraps, missing freshness markers or a change of source make the
  window invalid. A single interval is labelled lower precision.
- **Capacity.** Top-level `AppleRawCurrentCapacity`/`AppleRawMaxCapacity` if present, otherwise
  `BatteryData.RemainingCapacity`/`FullChargeCapacity`; the source is reported. Remaining hours =
  remaining Wh / mean W. macOS's own estimate (`pmset -g batt`) is shown alongside.
- **Before/after.** Applying never waits for a measurement. With the comparison enabled
  (`[measurement] automatic = true`, the TUI checkbox or `--measure`), the TUI records the battery
  reading when it opens and uses the existing 30-second refresh: if a fresh interval completes
  while you review the plan, it is the "before" value, otherwise "before" is unavailable. After
  applying, the first reading is discarded as possibly cached, and the "after" value appears on
  the session screen when a real interval completes. A difference smaller than 1 W or 10% of the
  "before" value (whichever is larger) is shown as "within measurement error", and a comparison
  based on one interval each is labelled lower precision. The CLI `on` command measures only with
  `--measure`, using the bounded blocking window above. A short comparison does not prove
  causality.
- **Remaining time.** The session screen and `status` share one calculation. If the Mac slept
  during the session (`sysctl kern.sleeptime` and `kern.waketime`, read instantly), only
  complete intervals after the last wake are used, labelled "at the average since waking". Until
  such an interval exists, the session average including sleep is shown without a remaining time.
- **TUI.** While open, the TUI reads ioreg every 30 seconds and labels the value as a cached
  snapshot. The energy ranking runs only when its button is pressed.

```toml
[measurement]
seconds = 180        # maximum wait per window
interval = 5         # polling interval, at least 5
updates = 2          # fresh updates required, 2–720
settle_seconds = 10  # wait after applying, before the second window (CLI --measure)
automatic = false    # passive before/after comparison in the TUI
```

Tool overhead measured on 2026-10-15 (CPU time, not energy): 10 ioreg calls and one pmset call over
20 s used 0.18 s of CPU; an idle open TUI over 300 s used 1.12 s. Reproduce with
`uv run python scripts/measure_overhead.py probes|tui --output FILE`.

## Low Power Mode

Apple describes Low Power Mode as reducing energy use to increase battery life
([Battery settings](https://support.apple.com/guide/mac-help/change-battery-settings-mchlfc3b7879/mac),
[Power Modes](https://support.apple.com/en-us/101613)) and does not list specific effects. Reported
effects differ by model:

- [Eclectic Light, January 2025](https://eclecticlight.co/2025/01/06/power-modes-and-apple-silicon-gpus/):
  on the M3 Pro tested, the older mode dimmed the display without a measurable GPU performance
  change; on the M4 Pro, the newer mode reduced GPU frequency and power.
- [9to5Mac, September 2024](https://9to5mac.com/2024/09/23/apple-is-making-it-easier-for-mac-users-to-enable-low-power-mode-with-macos-15-1/):
  some background tasks may be suspended and ProMotion displays limited to 60 Hz.

Our measurement on one MacBook Pro (M1 Pro, macOS 27, battery, browser and editor running), each
stage waiting for at least three fresh telemetry updates:

| Run | Off | On | Off |
| --- | --- | --- | --- |
| 1 | 18.85 W | 16.69 W | 17.60 W |
| 2 | 18.08 W | 18.12 W | 17.45 W |

The two runs point in opposite directions and the difference is within the variation between the
"off" stages, so no consistent effect was measured on this machine and workload. An earlier run
with heavy background jobs (a LaTeX build, a system compiler service) was discarded because its two
"off" stages differed by 5.5 W.

`scripts/measure_lowpower.py` (maintainers only; `--plan` shows the commands) repeats this test. It
needs battery power, no active travel-mode session and passwordless permission for
`/usr/bin/pmset`. Its only write is `pmset -b lowpowermode 0|1` in an off → on → off sequence; on
success, error or Ctrl-C it restores the original value and compares the whole battery section
byte for byte.

## Session reports and history

A session starts with its first action, not when the TUI opens. If a session is already active
when a scenario is started again, the CLI and TUI ask first, before any change: restore it and
start a new one (default), continue it (`--continue-session`; the original scenario and start time
are kept and the report lists the appended scenario as `appended_scenarios`), or cancel. With
`--yes` and no `--continue-session` the command cancels and points to `travel-mode off`. Times are
stored with their time zone and shown in local time.
 Start and end each take one ioreg
and one pmset reading; nothing is sampled in between.

- Whole-session average power comes from the system-load accumulator at start and end
  (`PowerTelemetryData.SystemLoad`). Without it, an endpoint capacity/voltage estimate is used,
  labelled `capacity_delta`, only when the retained pmset log shows the Mac on battery throughout.
- If AC power was observed, the report says the discharge statistics are incomplete and leaves out
  whole-session discharge and watts.
- Away reports add the complete sleep segments and battery DarkWake reasons from the power log
  within the session; partial segments are not extrapolated.
- Action counts are top-level actions; restore counts accumulate across retries.

The printed report uses local time, a readable duration and battery percentages, for example
"10 月 17 日 16:26 – 10 月 20 日 09:01（2 天 16 小时 34 分）". Failures that a later retry fixed
are listed as "failed, recovered after retry" rather than as failures. `off --verbose` and
`history --verbose` add commands, exit codes, capacities in mAh and raw reasons; the JSON fields
are unchanged. Older records that only stored a timeout are shown as "the first attempt did not
complete".

Power source for a session: the start and end observations plus the power-source events in the
retained pmset log. macOS writes those events only when something changes, so a session with no
events in its interval and battery at both ends counts as battery only; an AC event or an AC
observation marks it as having been on AC; an unreadable log makes it unknown. The log is read
once, when `off` writes the report, with a 10-second limit (reading it takes about 3 seconds on the
tested Mac); a timeout is reported as "power records could not be read" and does not affect the
restore. `status` and the TUI never read it.

Reports are appended to `history.jsonl` next to the state file (`[history] retain = 50`).
`history` compares the current report with earlier ones of the same scenario, scope and power
source; `difference_watts` in JSON is positive when power was lower. Reports contain local app and
service names; review them before sharing. If writing history fails, the state file is kept and
the next `off` retries the same report.

## Event automation

`automation install` writes one user LaunchAgent and bootstraps it in your GUI session. Its only
trigger is the launchd `com.apple.notifyd.matching` event for `com.apple.system.powersources.source`;
it has no `KeepAlive`, `RunAtLoad` or timers. On each event launchd starts the handler, which
consumes the XPC event, reads the power source, acts and exits. A 30-second throttle and
de-duplication limit bursts; notifications can be coalesced, so the handler reconciles the current
state rather than replaying every change. Research and sources:
[automation-research.md](automation-research.md).

```toml
[automation]
on_battery = "notify"   # notify | apply-work-safe | off
on_ac = "notify"        # notify | off-restore | off
retain = 50             # local event receipts kept
```

`apply-work-safe` turns on Low Power Mode and unloads selected user LaunchAgents; apps are quit or
frozen only if listed in `[apps].quit`/`[apps].freeze`. It never prompts for a password: if
passwordless permission is missing, that step is skipped and the notification says so. Daemon
apps, indexing, brightness, display sleep and network changes are never automated. `off-restore`
runs the normal restore with `sudo -n`; anything it cannot restore stays for a manual `off`.
Each handler allows at most 45 seconds of external commands; results go to `automation.json` next
to the state file.

Verified on a MacBook Pro (M1 Pro, macOS 27) with a separate test agent: unplugging started the
handler once, it recorded the battery source, exited with status 0 and left no running process.

## Menu bar

The menu bar item is a [SwiftBar](https://swiftbar.app/) plugin
([travel-mode.5m.sh](../integrations/swiftbar/travel-mode.5m.sh)); SwiftBar has to be installed
separately (`brew install --cask swiftbar`) and given a plugin folder.

- `menubar install` reads the plugin folder from SwiftBar's preferences
  (`defaults read com.ameba.SwiftBar PluginDirectory`) and fails with an explanation if none is
  set. It copies the plugin bundled with the installed package into that folder; it does not
  create a symlink, so the plugin keeps working if the source checkout moves. A different file
  with the same name is replaced only with `--force`. `--dry-run` shows the target and writes
  nothing. SwiftBar is not started or refreshed for you.
- `menubar uninstall` removes only a file carrying the travel-mode plugin marker; anything else
  at that path is reported and left in place.
- `menubar status` shows the target path, whether the plugin is installed and executable, and
  whether the installed copy matches the version bundled with the current package.

The battery line does not show macOS's instantaneous estimate, which jumps with the current load.
During an active session that has not been seen on AC power, it divides the remaining energy by
the session's average power (from the battery's accumulated telemetry: one extra `ioreg` read,
2-second limit) and says so ("Battery 62% · about 3 h 40 min at this session's 9.8 W"). Without a
session, or when the data is insufficient or AC power was seen, it shows only the percentage. The
tool runs only when invoked, so a charge while it was not running cannot be detected; the estimate
then reflects only what it observed. `status --json` adds `average_watts`, `estimate_source`,
`estimate_error` and `system_remaining_minutes` (macOS's own estimate, for diagnosis).

The `5m` in the file name sets the refresh interval. Each refresh runs `travel-mode status --json`,
which reads the configured language, the local state file and one `pmset -g ps` (2-second limit);
on the tested Mac a refresh takes about 0.1 s in total. Health check, power measurement and the
other menu items run only when clicked, in a terminal, and refresh the plugin afterwards.

The title is a monochrome SF Symbol that follows the light or dark menu bar: an outline leaf when
no session is active, a filled leaf (work) or a moon (away) with the elapsed time during a session,
and a warning triangle when `travel-mode` cannot be found or its status cannot be read. Menu text
uses the configured language. SwiftBar's own menu items are hidden through plugin metadata.

SwiftBar runs plugins with a short `PATH`, so the plugin looks for the executable in this order:
`TRAVEL_MODE_BIN`, `~/.local/bin/travel-mode` (uv tool and pipx), `/opt/homebrew/bin/travel-mode`,
`/usr/local/bin/travel-mode`, then `PATH`. If none is found, the menu shows the install command.

`status --json` fields used by the plugin include `active`, `scenario`, `started_at`,
`elapsed_seconds`, `duration_minutes`, `power_source`, `battery` (`percent`, `remaining_minutes`,
`charging_state`) and `app_version`. Fields macOS does not report are `null`. The schema is
[status.schema.json](json-schema/status.schema.json).

## Passwordless sudo

`sudoers install` writes only `/etc/sudoers.d/travel-mode`, validates it with `visudo`, shows the
full policy and requires typing `yes`; replacing an existing file needs `--force` and a second
`yes`. The default scope allows every `pmset` operation, including resetting to defaults. The `all`
scope adds `launchctl load/unload` of any plist (which can run code as root), `kill -TERM` of any
process as root, indexing and Wi-Fi changes; its argument patterns are broader than the tool's own
checks. See [sudoers.md](sudoers.md).

## Restore and recovery

Recovery intent is saved before each change. Power records hold only the keys that changed,
separately for battery and AC. `off` restores those values, resumes recorded process identities
(PID plus start time and executable), reloads unloaded services, reopens quit apps and restores
network, brightness and indexing. LaunchAgents are paused with unload/load, never `launchctl
disable`.

- Each restored item is checked off; failures stay for another `off`. `off --force` discards
  outstanding records after a failed restore.
- Terminated `caffeinate` processes and immediate display sleep are not reversed. A reopened app
  does not recover unsaved documents or window positions.
- Snapshots older than 24 hours warn that restoring can overwrite later changes to the same keys.
- Without a snapshot, non-interactive `off` fails; interactive `off` offers a separately confirmed
  `sudo pmset restoredefaults`.
- `off` resumes only the processes travel-mode recorded. `thaw` is a separate tool for any stopped
  app process: it lists all of them (including ones stopped by other programs) and resumes them
  after confirmation; `thaw --all` resumes without asking.
- There is no cross-process lock: run one modifying instance at a time.

## Configuration and environment

| Variable | Effect |
| --- | --- |
| `TRAVEL_MODE_CONFIG`, `TRAVEL_MODE_STATE` | Configuration and state paths (XDG roots are also honored) |
| `TRAVEL_MODE_LANG` | `en` or `zh`; overrides `options.language` |
| `TRAVEL_MODE_INTERFACE` | `cli` or `tui` |
| `TRAVEL_MODE_APP_DIRS` | Extra absolute app directories, colon-separated |
| `TRAVEL_MODE_DEBUG=1` | Show full tracebacks for configuration, state and measurement errors |

`suggest --write` asks for confirmation, keeps comments and validates before replacing the file.
Pressing `L` in the TUI saves `options.language`, keeping comments. JSON schemas are in
[json-schema/](json-schema/).

## Failed actions and the log

An action that fails (a command exits non-zero, an app refuses to quit, a service cannot be
unloaded) is shown on one line with the reason; the other actions in the same batch continue and
the batch ends with a summary such as "4 succeeded, 1 failed". A state file that cannot be read or
written stops the remaining changes, because their recovery could not be recorded. An unexpected
internal error keeps the TUI open and shows one line with the log path; set `TRAVEL_MODE_DEBUG=1`
to see the traceback instead.

Independent actions in a batch run at the same time: quit requests go to all apps at once and
share one 10-second verification deadline, and freezes and LaunchAgent unloads run in parallel.
Steps for the same app, plist or settings domain keep their order; power settings remain one
`pmset` command. Each action's recovery intent is written to the state file by a single writer
before its command is sent. If a batch must stop, no new action starts, running ones finish, and
the original error is reported. The CLI prints results in plan order. `off` reloads services and
reopens apps in parallel; reopen requests are sent in the recorded order. A retry marks only its
own earlier failure as recovered, matched by action type and exact target, not by display text.

Quitting an app uses AppleScript. After the request the tool watches the app's main process for
up to 10 seconds, also when AppleScript reported -128 or a timeout: some apps reply "cancelled"
and then quit on their own. Helper processes that stay for a moment after the main process exits
are mentioned but do not count as a failure. If the main process is still running at the end (for
example while the app shows a confirmation dialog), `away` force-quits it and reports a
warning; `work` marks the item failed and offers retry and force quit, and never force-quits on
its own.

Failures are appended as JSON lines to `travel-mode.log` next to the state file (mode 0600,
rotated once at 1 MiB): time, scenario, action, command, exit code, reason, and stdout/stderr
excerpts, plus the traceback for internal errors. The session state, the report printed by `off`
and `travel-mode history` list the failed actions.

## App defaults and setup

`travel-mode setup` (shown automatically on the first run without a configuration file) lists
installed and running apps with a recommended action for `work` and `away` and a one-line reason:

| App | work | away |
| --- | --- | --- |
| Ordinary app | freeze | freeze |
| Cloud-file provider (`file_provider = true` in categories.toml: Google Drive, OneDrive, Dropbox, Box) | quit | quit |
| Holds a sleep assertion | freeze, with a note | quit |
| Browser, editor, terminal | ask | ask |

Protected apps (the terminal running the tool and its ancestors, the frontmost app, protected
Apple apps) are not listed. Explicit configuration always wins over a recommendation, and
`[apps.work]` / `[apps.away]` override the scenario-independent `[apps]` lists. Configuration
files written before this feature have no `apps.policy` key and keep the previous built-in
defaults (`legacy`); new files and files saved by setup use `adaptive`. Setup writes atomically,
keeps comments, and with `--dry-run` only prints what it would write.

## Exit codes and errors

| Code | Meaning |
| --- | --- |
| 0 | Completed |
| 1 | Operation failed, restore incomplete, or unexpected error |
| 2 | Configuration or argument error; unsupported platform or missing system capability |
| 3 | Invalid, unreadable or unwritable state |
| 4 | Battery measurement read or parse error |
| 5 | Automation installation or event handling error |
| 6 | Sudoers generation, validation or operation error |

Configuration, state and measurement errors print one line with the file, key and reason;
`power --json` returns an error object. Other unexpected errors print a normal traceback.
