# travel-mode

**English | [简体中文](README.zh-CN.md)**

`travel-mode` adjusts macOS power settings and pauses selected background programs, with a snapshot for restoring its changes.

## Use cases

**Work on battery (`work`).** For meetings, venues and other places without a charger, keep the screen and network available while pausing recognized sync, backup and update programs and explicitly configured heavy apps. Default power changes enable battery Low Power Mode only. Existing display/system sleep timers, hibernation mode, AC settings, Wi-Fi, Bluetooth and brightness remain as configured. No process is started to keep the display awake. Spotlight indexing is optional and requires selection or confirmation.

**Away / overnight (`away`).** Prepare for lid-closed travel or overnight sleep using the existing sleep-oriented power recommendations. Review app, service and network actions before applying them. The `flight` and `overnight` profiles belong to this scenario; `flight` also requests network disconnection, hibernatemode 25, lower brightness and stopped indexing.

The tool does work while a command or the terminal user interface (TUI) is open. It installs no resident service, daemon or periodic polling task. Battery life depends on hardware, battery condition and foreground workload; no 7–8-hour runtime has been established.

## Install

Install with one of these alternatives:

```bash
uv tool install git+https://github.com/yulong-ge/travel-mode
```

```bash
pipx install git+https://github.com/yulong-ge/travel-mode
```

Python 3.10–3.13 and macOS are supported. The commands above install from the repository. Bluetooth control optionally uses `blueutil`; brightness control uses the available brightness adapter. Missing capabilities are shown in the interface. Development setup: [CONTRIBUTING.md](CONTRIBUTING.md).

## 30-second start

```bash
travel-mode check
travel-mode on --scenario work --dry-run --yes
travel-mode
travel-mode off
```

The bare command first shows two scenario buttons. Select Work on battery or Away / overnight, review the actions, then apply selected groups. `--scenario work|away` and `--profile NAME` skip this screen. `check` observes the machine; `off` restores recorded changes. Without an interactive terminal, the bare command runs `check`.

## Use CLI prompts

```bash
travel-mode --no-tui
travel-mode on --scenario work --yes
travel-mode config set-interface cli
travel-mode --tui --scenario away
```

The first CLI question selects the scenario when `options.scenario` is `"ask"`. `--yes` requires `--scenario`, `--profile`, or a configured scenario; it does not silently choose one. In work mode, `ask` apps and optional Spotlight are skipped by `--yes`. Explicit quit/freeze app settings still apply. Work batches do not force quit automatically; a failed quit stops the batch.

Interface priority is `--tui` / `--no-tui`, then `TRAVEL_MODE_INTERFACE=cli|tui`, then `options.interface` (default `"tui"`). A non-interactive terminal falls back to `check`, except explicit `--no-tui` and `--yes`, which request the apply flow.

## Changes, permissions and safety

Apple command-line tools use fixed system paths at execution time, including `/usr/bin/sudo` and its system-tool targets. Missing system paths fail explicitly without PATH fallback. Optional `blueutil` and `brightness` executables use PATH; `check` shows their resolved paths and `check --json` includes `tool_paths`. Review these locations before using third-party controls. FakeRunner records and dry-run commands retain logical tool names.

Work defaults enable battery Low Power Mode, select configured categories, unload explicitly configured user LaunchAgents, and apply explicit app quit/freeze choices. They preserve foreground app defaults and network availability. A profile can supply a brightness target between 0 and 1; ordinary work brightness is unchanged. Work power recommendations ignore sleep/display/hibernation overrides. The Power and Network pages still expose separately selected manual actions.

Away actions can change battery settings, AC `powernap`/`womp`, global `disablesleep`, apps, launchd services, indexing, Wi-Fi/Bluetooth and brightness. They can also terminate selected `caffeinate` assertion holders or sleep the display. `--yes` accepts defaults, including AC changes and the existing force-quit fallback. `--aggressive` additionally permits the optional aggressive CLI actions. Profiles can request actions independently. Review a dry run after changing configuration. Save documents before quitting; force quit can lose work, freezing can retain sleep assertions, and network changes can disconnect remote sessions.

Sudo is requested only for operations that require it, such as power writes, global sleep repair, system-service operations, indexing, Wi-Fi and signaling root-owned processes. The tool checks permission for the exact command with non-interactive sudo. Opening the TUI does not request credentials. Graceful app quit may need macOS Automation permission.

`--dry-run` records mutation commands and isolates configuration, migration and recovery writes in a temporary directory. It still performs observations and can request sudo credentials for a privileged observation in an interactive terminal. Unreadable observations are reported as uncertain and produce a nonzero result. A preview does not establish that a later real operation will succeed.

## Commands

| Command | Purpose |
| --- | --- |
| `travel-mode` / `travel-mode on` | Select a scenario, then use the chosen interface |
| `travel-mode off` / `travel-mode restore` | Restore outstanding recovery records |
| `travel-mode power --seconds 20` / `--json` | Measure battery draw in a finite window |
| `travel-mode check --reapply` | Pause recorded items detected running again |
| `travel-mode check` / `travel-mode check --json` | Read-only audit, including the effective saved scenario in JSON |
| `travel-mode report` / `travel-mode report --json` | Read-only sleep / DarkWake report |
| `travel-mode config path` | Show configuration and state paths |
| `travel-mode config init` | Write the template; `--force` overwrites it |
| `travel-mode config validate` | Validate configuration |
| `travel-mode config edit` | Open `$VISUAL` or `$EDITOR`, then validate |
| `travel-mode config set-language en` / `zh` | Save language |
| `travel-mode config set-interface cli` / `tui` | Save interface |
| `travel-mode thaw` / `travel-mode thaw --all` | Resume recorded apps / review all stopped app processes |
| `travel-mode profiles` | List profiles |
| `travel-mode suggest --samples 3` | Read-only energy and category suggestions |
| `travel-mode suggest --json` | Structured suggestions |
| `travel-mode suggest --write` | Confirm additions to app/service/daemon configuration |
| `travel-mode history [--json] [--limit N]` | Read local session history |

Other options: `--scenario work|away`, `--profile NAME`, `--dry-run`, `--yes` / `-y`, `--aggressive`, `--lang en|zh`, `--help`, `--version`. `--force` with `off` discards outstanding recovery records even after a restore failure. `thaw --all` can resume apps stopped outside this tool. Pressing `L` in the TUI saves the selected language to `options.language` in the configuration file and preserves TOML comments. In dry-run, it writes only the isolated configuration copy. Hidden `--demo` uses fixtures for screenshots.

## Configuration and categories

Paths default to `~/.config/travel-mode/config.toml` and `~/.local/state/travel-mode/state.json`. Override them with `TRAVEL_MODE_CONFIG` and `TRAVEL_MODE_STATE`; XDG roots are supported. See the [complete example](examples/config.toml).

```toml
[options]
scenario = "ask" # ask | work | away
interface = "tui" # tui | cli
language = "auto" # auto | en | zh

[measurement]
seconds = 180
interval = 5
updates = 2
settle_seconds = 10

[categories]
pause = ["sync", "backup", "updater"]
ignore = [] # rule ids or public names from categories.toml

[services]
unload = []
keep = []

[apps]
default = "ask"
quit = []
freeze = []
skip = []

[profiles.work]
# brightness = 0.5
```

The packaged [category registry](src/travel_mode/categories.toml) defines `sync`, `backup`, `updater`, `vm` and `keep-awake`, with public source references for each rule. It matches launchd labels, bundle IDs, exact app names and executable paths. `suggest`, the Services page and the Apps page group recognized items by category. Only loaded plist-backed LaunchAgents in `~/Library/LaunchAgents` and `/Library/LaunchAgents` are category unload targets. System LaunchDaemons are not automatically unloaded by categories. Recognizing a product does not establish that all of its components can be paused.

`categories.pause` selects automatic category actions in work mode; `vm` is opt-in. Category buttons provide Pause sync and backups and Pause updaters in either scenario. The work overview checks category groups by default. Individual category selections in `pause` are respected by that overview. iCloud and Time Machine are observation only. Some backup products and embedded updaters are identification-only rules; manage those within the product. Category quit is graceful and does not force quit.

Category actions supplement `services.unload` and explicit `apps.quit` / `apps.freeze`; repeated identities are processed once per batch. `services.keep` wins over unload selectors and category actions. `apps.skip` and `apps.ignore` protect apps and matching category agents. Explicit app actions, including `ask`, take priority over category defaults. `categories.ignore` suppresses automatic category actions and category buttons, while explicit service/app settings still apply. Identification-only rules never become category actions. Existing `apps.with_daemon` configuration remains an explicit, separately configured mechanism.

Built-ins are `work`, `flight` and `overnight`. `cafe` was merged into `work` and removed, with no alias. A profile overrides ordinary settings for its supplied fields; custom `[profiles.NAME]` values override the matching built-in. A custom profile defaults to away unless it sets `scenario = "work"`. Conflicting explicit `--scenario` and profile scenarios are errors. Snapshots record the last applied scenario while keeping the first original values for recovery. `check --json` uses null when no saved scenario exists, including older snapshots.

`TRAVEL_MODE_LANG` overrides configured language. `TRAVEL_MODE_APP_DIRS` adds colon-separated absolute app directories; empty/relative entries are errors. `suggest --write` requests confirmation, preserves comments and validates before replacing configuration. JSON schemas: [check](docs/json-schema/check.schema.json), [suggest](docs/json-schema/suggest.schema.json). Optional integrations: [Shortcuts](integrations/shortcuts.md), [menu plugin](integrations/swiftbar/travel-mode.10s.sh), [sudoers](docs/sudoers.md).

## Health check

`travel-mode check` includes a read-only health section; `travel-mode check --json` exposes the same structured findings under `health`. The overview checks once when opened and on an explicit refresh. It shows each actionable finding with a button that uses the existing Action/Executor and recovery journal. No health timer, resident process or service is installed. The existing 30-second overview timer still reads only the battery.

The nine checks cover global SleepDisabled, caffeinate and known keep-awake apps, non-system system-sleep assertion holders, online external displays, Spotlight CPU activity, a running Time Machine backup, battery Low Power Mode, native display brightness and background CPU activity. Each result contains a status, evidence, an impact explanation and a handling instruction. Failed or malformed reads show **Cannot check** with the command and reason; other checks continue. Each check has a two-second command deadline, within a fifteen-second total command budget. Reports include elapsed wall time. Parsing and rendering add a small amount of time outside command execution. If a shared read prevents the older audit from completing, JSON includes `audit_error` and the complete health report, without invented legacy values. A TUI recovery-snapshot read failure leaves the diagnostics available and disables handling actions until the error is fixed and the tool is reopened.

The shared process snapshot uses one `ps` call, without `top` or a sampling delay. Its `%CPU` estimate is not instantaneous watts. Background CPU results exclude other users; executable paths under `/System`, `/usr`, `/bin`, `/sbin` and `/Library/Apple`; this tool and its ancestors; stopped processes; Terminal, iTerm/iTerm2, Warp, Alacritty and kitty; and the foreground app and helpers in its bundle. If foreground identification fails, that check reports an error. Up to five processes are listed with their known sync/backup/updater/vm category, where available. App targets use existing quit/freeze actions; other processes require inspection in Activity Monitor. Explicit app skip/ignore and category ignore settings suppress health action buttons too. Identification-only category rules remain observation only. Configured daemon apps use the existing daemon shutdown Action.

Spotlight enabled alone is not a hit: the total reported CPU of `mds`, `mds_stores`, `mdworker` and `mdworker_shared` must reach the threshold. The stop-indexing action requests sudo only when selected. Assertion holders use quit rather than freeze because freezing does not release an assertion. caffeinate evidence includes its full command line and parent; termination also rechecks the existing exact PID identity and sleep assertion. A keep-awake app's presence does not prove its session is active. Names and public sources are recorded in the `keep-awake` category; this category is not paused by default.

External displays use a short-lived, timeout-bounded CoreGraphics reader. Display IDs identify online non-built-in displays, including virtual displays; they do not prove that a physical dock is attached. Time Machine and displays have manual instructions only. Native brightness is checked only on battery; an unreadable native backend produces an error. Keyboard backlight and Bluetooth wake permissions are omitted because no reliable unprivileged reader was verified.

```toml
[health]
brightness_threshold = 0.7
brightness_target = 0.5
cpu_threshold = 20
spotlight_cpu_threshold = 20
```

Brightness thresholds and targets are fractions greater than zero and at most one; CPU thresholds are percentages greater than zero and at most 10000. A selected profile's brightness target takes precedence over `health.brightness_target`. Automatic buttons are optional; checks themselves do not change settings. `off` restores recorded changes, with the existing exception for terminated caffeinate processes.

## Power measurement

`travel-mode power` waits for two fresh telemetry updates, covering at least one interval. It reads `ioreg -rn AppleSmartBattery` every 5 seconds and stops after 180 seconds by default. On the recorded hardware, macOS refreshed these registry values every 20–60 seconds; repeated reads returned the same cached data. Allow about 1–3 minutes per window. The first snapshot is only a freshness reference: the first observed change establishes the baseline, so an interval predating an Action is excluded. At a slow update cadence the timeout can expire before two updates; this returns **insufficient samples**, without average watts or a runtime estimate. `travel-mode power --seconds 240 --updates 3 --json` extends the maximum wait and returns structured results, as defined by the [power schema](docs/json-schema/power.schema.json). No sudo is needed. AC, charging or no battery produces an explicit unavailable result.

The preferred source is `PowerTelemetryData.AccumulatedSystemLoad` and `SystemLoadAccumulatorCount`: interval mean W = difference of accumulated mW / difference of sample count / 1000. The overall mean weights intervals by their accumulator counts. When available and advancing, signed battery discharge/power accumulators provide a separate battery mean. Every result identifies the power and capacity source, actual waiting time, covered observation span and interval count. A single interval is labelled “Only 1 interval; lower precision.” The covered span is between observed hardware changes, with up to one polling interval of boundary uncertainty; it is not a claim that the registry updates continuously.

Only when the system-load accumulator pair is absent does a window use voltage × signed current, integrated between fresh `UpdateTime` observations. This fallback is explicitly labelled `voltage_current`. Unsigned 64-bit negative current is decoded first. The recorded current can be quantized and is less reliable than the load accumulator. A partial/malformed selected source is an error. Missing freshness markers, counter resets/wraps or a source change prevent a valid average; the tool does not infer a counter epoch or combine different sources. Duplicate counter/timestamp values never become new samples.

Capacity selection uses top-level `AppleRawCurrentCapacity`/`AppleRawMaxCapacity` first; only if both are absent does it use `BatteryData.RemainingCapacity`/`FullChargeCapacity`. A selected layout must be valid; nominal/design or percentage capacities are not substitutes. Remaining Wh uses remaining mAh and the latest voltage; estimated hours are remaining Wh / interval mean W. macOS's own estimate is read separately with `pmset -g batt`. Load fluctuations, telemetry quantization, cached interval boundaries and future screen/network/foreground activity limit accuracy. A short before/after comparison does not prove causality or guarantee battery life.

On battery, work measures before applying Actions, waits 10 seconds to settle, then measures again with the same policy. The CLI and TUI show the expected wait before sampling; allow about 2–6 minutes for both windows (up to 370 seconds with default timeouts/settling). `--no-measure` skips the comparison; `--measure` enables it in away too. The TUI shows fresh-update progress and remains responsive. Auxiliary read failures or insufficient samples remain in the session report while Actions continue; an invalid first window prevents an after-window comparison. AC interrupts the window. Dry-run skips the comparison.

Configure `[measurement] seconds = 180` (maximum wait), `interval = 5`, `updates = 2`, and `settle_seconds = 10`. Values must be finite and at most 3600 seconds; timeout is positive, polling is at least 5 seconds, settling is nonnegative, and updates is an integer from 2 to 720. `power --seconds` and `--updates` override only that command. The open overview still reads ioreg every 30 seconds and labels its single registry snapshot as cached, rather than presenting it as an interval average. Energy ranking runs only on its button. No sampler runs after the command/TUI closes.

Opening the tool or running `check` also checks recorded paused items for resurrection. Use the existing reapply button or `travel-mode check --reapply`; this is not continuous monitoring.

Earlier read-only overhead measurements on 2026-10-15 used a **raw-probe benchmark**, not the current fresh-update window: 10 ioreg calls and one pmset call over 20.159 seconds used 0.020392 seconds of tool CPU and 0.155477 seconds of child CPU, total 0.175869 seconds. An idle open TUI over 300.001 seconds used 0.931522 + 0.189740 = 1.121262 CPU seconds, with 10 ioreg calls and no top. These are historical CPU times, not energy measurements or a benchmark of the new 180-second policy. Reproduce the raw-probe/TUI accounting with `uv run python scripts/measure_overhead.py probes --output /tmp/travel-mode-probes.json` or `uv run python scripts/measure_overhead.py tui --output /tmp/travel-mode-tui.json`; the scripts isolate paths, install a read-only Runner and measure `resource.getrusage` for the tool and its children.

## Session reports and history

Apply checks `pmset -g ps` before sampling. External power skips the measurement window. A failed power-source query, battery read or parse reports the reason and continues the power-saving actions. The session report retains the failed measurement without estimated savings. If the first window fails, the second window is not attempted. Cancelling a measurement and unexpected programming errors still interrupt the operation. The standalone `power` command reports read failures as errors. The overview displays failed battery reads without closing the TUI.

`travel-mode off` shows the session scenario, UTC start/end times, duration, battery endpoints in percent and raw mAh, available power estimates, attempted/successful/failed Action counts, restore results and previously observed resurrections. The TUI shows the same report in a scrollable view after restore. The Network/restore page has a read-only **Session history** button.

A session begins at its first Action, not when the TUI is opened. Start/end readings use one ioreg and one pmset battery query each. The tool does not sample while closed. `check` and the open TUI retain resurrections they actually observed; this is not continuous monitoring. The existing TUI battery timer also records external-power observations without another probe. Counts refer to top-level Actions, not subprocess or PID counts. Failed attempts are retained. Restore counts are separate and accumulate across retries.

Whole-session power uses the difference of the system-load accumulator and count captured at session start/end; no continuous sampler is needed. The report labels this source `PowerTelemetryData.SystemLoad`. On hardware without that accumulator, the existing endpoint capacity/voltage estimate is labelled `capacity_delta`. It is available only with valid endpoint fields, no increasing charge/capacity, and a battery-source anchor in the retained pmset log at or before the start with no recorded AC interval. Source classification depends on the retained log and cannot prove that events were never omitted. If AC was observed, the report says discharge statistics are incomplete and omits whole-session discharge and watts. Missing source coverage or readings also produce explicit unavailable values. Endpoint readings remain visible and are not described as discharge on AC. Valid before/after measurement windows are shown separately; an after-window average can be compared only with other after-window averages from the same power source, never with whole-session estimates. Historical averages are grouped by scenario, scope and power source. JSON `difference_watts` is positive for lower power and negative for higher power; text displays the direction and absolute difference.

Away reports reuse the sleep-segment and DarkWake parsers, restricted to the session interval. Only complete recorded sleep segments are summed. Segments crossing a boundary are not extrapolated; no matching complete segment means unknown discharge, not zero. Recorded AC or unknown source coverage suppresses aggregate sleep-discharge statistics. DarkWake counts/reasons refer only to recorded battery events in the interval.

```bash
travel-mode history
travel-mode history --limit 10
travel-mode history --json --limit 10
```

Reports are stored locally in `history.jsonl` beside the active state path (`TRAVEL_MODE_STATE` also isolates history). `[history] retain = 50` keeps the newest 50 reports by default; valid values are 1–10000. `history` shows the newest 50 by default; `--limit N` changes the display limit. JSON follows the [history schema](docs/json-schema/history.schema.json). Comparisons exclude the current report and use retained reports with the same scenario and statistical scope. Missing averages are excluded. History reads do not probe macOS or migrate configuration.

The report is checkpointed before an atomic history write and state deletion. If archival fails, the state remains, further Actions are blocked, and another `off` retries the same report without changing its end readings or duplicating history. Partial restores retain their session and receipts. Dry-run does not sample or archive sessions. Older snapshots have unknown start readings/action counts; their reports state this explicitly. Reports contain local app/service names and should be reviewed before sharing. There is no cross-process writer lock; use one modifying instance at a time.

Health JSON retains the complete `health.checks` observations and adds `health.display_checks` with deduplicated evidence and `references`. CLI and TUI use this display view; a process appears first in keep-awake tools, then assertions, then background CPU in priority order. The overview combines status and health in one card.

## Restore and recovery

Recoverable actions save recovery intent before mutation. Power records contain only the keys actually changed, separately for battery and AC. `off` restores those values, resumes recorded process identities, reloads unloaded services, reopens quit apps and restores changed network/brightness/indexing settings. LaunchAgents use unload/load; the tool does not use launchctl's disable mechanism.

Each successful restore is checkpointed. Failed records remain for another `off`. Reopening an app does not recover unsaved documents or exact window state. Terminated `caffeinate` processes and immediate display sleep are not reversed. Snapshots older than 24 hours warn because recovery can overwrite later changes to recorded keys. There is no cross-process lock; use one modifying instance at a time.

Without a snapshot, non-interactive `off` fails without resetting settings. Interactive `off` offers a separately confirmed factory power reset. Preserve damaged snapshots and repair the indicated issue before retrying. Use `travel-mode thaw` for recorded frozen apps; resume other stopped processes only after reviewing their identity. If brightness recovery is unavailable, use System Settings.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Command completed |
| 1 | Operation failed, restoration remains incomplete, or an unexpected exception occurred |
| 2 | Configuration / argument error; also unsupported platform or missing platform capabilities |
| 3 | Invalid, unreadable or unwritable recovery state |
| 4 | Battery power measurement read or parse error |

Configuration and state errors print one diagnostic line with a path, relevant key/reason and repair guidance, without a Python traceback. Set `TRAVEL_MODE_DEBUG=1` to retain the full traceback and exception chain. Battery measurement errors also print one diagnostic line; `power --json` returns an error object with `available=false`, `reason="failed"` and `error.type`/`error.message`. Debug mode retains their full traceback and exception chain. Unexpected exceptions retain their normal traceback. An external editor can return its own exit code.

## Compatibility and verification limits

The repository records earlier maintainer hardware checks on 2026-10-15: macOS 27.0.1, arm64. These are historical results; this scenario/category revision does not repeat system mutations.

| Previously hardware-verified with temporary fixtures | Coverage |
| --- | --- |
| Apps and services | Graceful AppleEvent quit, exact-process force quit, main/helper freeze/resume, user LaunchAgent unload/reload, app reopen |
| Power and restore | Battery round trip, AC powernap/womp, global disablesleep, hibernatemode 25, original power output restoration |
| Recovery and brightness | CLI thaw --all, brightness set/restore |
| Interfaces | CLI prompts and TUI keyboard/mouse apply/restore |

Wi-Fi, Bluetooth, Spotlight, immediate display sleep, caffeinate termination and AC sleep/display timers were not hardware-verified. Work/category behavior is tested with isolated FakeRunner and demo fixtures, not actual product suspension or battery endurance. CI covers Python 3.10–3.13 on Ubuntu and macOS; Linux is a test host, not a supported runtime. Other hardware and macOS versions remain unverified.

## Contributing and license

[CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). [MIT](LICENSE).
