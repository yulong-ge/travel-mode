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

**work** enables battery Low Power Mode, selects the configured categories, unloads configured
user LaunchAgents and applies explicit app quit/freeze settings. Display and system sleep timers,
`hibernatemode`, AC settings, Wi-Fi, Bluetooth and brightness are unchanged; a profile can set a
brightness target between 0 and 1. Work power recommendations ignore sleep, display-sleep and
hibernation overrides. Spotlight indexing is optional and needs a selection or confirmation. The
Power and Network pages still offer manual actions.

**away** uses the sleep-oriented recommendations and can change battery settings, AC
`powernap`/`womp`, global `disablesleep`, apps, launchd services, indexing, Wi-Fi, Bluetooth and
brightness. `flight` also requests network disconnection, `hibernatemode 25`, lower brightness and
stopped indexing.

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

Apple tools (`pmset`, `ioreg`, `launchctl`, `ps`, `kill`, `osascript`, `sudo` and others) are run
from fixed system paths such as `/usr/bin` and `/usr/sbin`; a same-named program earlier in `PATH`
is never used, and a missing system tool is an error. Optional third-party tools (`blueutil`,
`brightness`) are found on `PATH`; `check` shows the resolved paths (`tool_paths` in JSON).

## Health check

The health section in `check`, `check --json` (`health`) and the TUI overview runs once when the
command or overview opens and on refresh. No timer or background process is involved.

Checks: global `SleepDisabled`; `caffeinate` and known keep-awake apps; non-system holders of
system-sleep assertions; online external displays; Spotlight CPU activity; a running Time Machine
backup; Low Power Mode off on battery; display brightness on battery; background CPU activity.
Each finding has a status, evidence, an impact line and either an action or manual steps.

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
- **Before/after.** In work on battery, apply measures one window, runs the actions, waits 10
  seconds and measures again (about 2–6 minutes in total). `--no-measure` skips it; `--measure`
  enables it in away. A measurement failure is reported in the session and does not stop the
  actions. A short comparison does not prove causality.
- **TUI.** While open, the overview reads ioreg every 30 seconds and labels the value as a cached
  snapshot. The energy ranking runs only when its button is pressed.

```toml
[measurement]
seconds = 180        # maximum wait per window
interval = 5         # polling interval, at least 5
updates = 2          # fresh updates required, 2–720
settle_seconds = 10  # wait after applying, before the second window
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

A session starts with its first action, not when the TUI opens. Start and end each take one ioreg
and one pmset reading; nothing is sampled in between.

- Whole-session average power comes from the system-load accumulator at start and end
  (`PowerTelemetryData.SystemLoad`). Without it, an endpoint capacity/voltage estimate is used,
  labelled `capacity_delta`, only when the retained pmset log shows the Mac on battery throughout.
- If AC power was observed, the report says the discharge statistics are incomplete and leaves out
  whole-session discharge and watts.
- Away reports add the complete sleep segments and battery DarkWake reasons from the power log
  within the session; partial segments are not extrapolated.
- Action counts are top-level actions; restore counts accumulate across retries.

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

Quitting an app uses AppleScript. If the app cancels the request (error -128, for example while
it shows a confirmation dialog) or does not answer in time, `away` force-quits it and reports a
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
