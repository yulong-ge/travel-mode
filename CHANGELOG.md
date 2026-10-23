# Changelog

All notable changes follow Keep a Changelog.

## [Unreleased]

### Fixed

- The menu bar no longer shows a warning on AC power without a session, and refreshing it no longer launches SwiftBar when it is not running; `menubar status` reminds you to enable SwiftBar's Launch at Login.
- The menu bar icon no longer leaves a gap to its right: the symbol is part of the title text. The menu explains a warning icon, shows when it was checked and offers "Restore system sleep" (new `travel-mode sleep repair`), and the icon refreshes as soon as power saving is turned on or off.
- The guarded e2e capability check waits for real applet exits instead of a fixed AppleEvent timeout, which failed on a slow runner.
- The foreground app is read through the documented `NSWorkspace` API, with `lsappinfo` only as a fallback; battery data is read from `ioreg -a` plist output instead of text. Apps installed in several copies (for example several Python versions) appear once in setup with a copy count, and actions apply to every copy.
- Apps are recognized by their bundle structure (`Contents/Info.plist`, type `APPL` or a declared executable), so folders that only end in `.app` (for example a PowerShell module `Az.App`) are skipped; one odd entry no longer aborts setup, check or suggest, and errors go to stderr. Parsers are tested against raw command output recorded on a clean macOS 26 runner.
- The app audit no longer fails on Macs whose `lsappinfo` prints quoted keys (seen on macOS 26): both observed layouts are parsed, and if the foreground app cannot be identified, app actions are blocked while the rest of the check still works. The clean-machine CI job reports the real error and collects raw command output from each runner.

### Changed

- Detection no longer depends on the maintainer's Mac: the hard-coded sleep-source special case and the built-in network/VPN name list are replaced by sourced rules (15 network, VPN, proxy and remote-access products), maintainer-specific app lists and backup-label guesses are removed (old configuration files keep their behavior), and the demo data is neutral.
- First-run setup checks the whole Mac in one read-only scan and groups apps, LaunchAgents, network apps and keep-awake tools with recommendations; "Add recommended" writes apps, services and the network list at once. `setup --yes` and `suggest --write` share the same logic.

### Added

- `[menubar] show = "session"` (`travel-mode config set-menubar session`, or "More" in the TUI and menu) shows the menu bar icon only during a power-saving session.
- CI covers macOS 15 (arm64 and Intel) as well as macOS 26, and the clean-machine job runs on each of them.
- A read-only clean-machine job on a GitHub macOS runner that runs check, setup preview and suggest and rejects any maintainer-specific term in the output.

## [1.2.0] - 2026-10-21

### Changed

- Applying no longer waits for a power measurement: the TUI records the "before" value passively while you review the plan and fills in "after" on the session screen; small differences are shown as within measurement error. The CLI measures only with `--measure`.
- Independent actions run in parallel: apps get their quit requests at once and share one 10-second deadline (10 apps: about 100 s -> 10 s in simulation), and restore reopens apps and reloads services in parallel.
- Remaining battery time after the Mac slept uses only readings since the last wake.
- Redesigned TUI: a plan screen (sleep readiness, health findings, one row per action with target and reason, filters instead of five tabs) that turns into a result screen, a session screen while a session is active, and a details drawer (`d`). Rows have exactly one state and only the controls that state allows; noise such as uninstalled network apps and unreadable plists is summarised in one dim line, and empty placeholder plists are ignored.
- Scenarios are shown as "On the go" (`work`) and "Overnight / flight" (`away`). `work` now also turns off Power Nap and wake for network access, which only act during sleep; `away` quits apps instead of freezing them and offers Wi-Fi/Bluetooth off unselected. Network keep-alive during sleep is never turned off because Find My needs it.
- Sleep readiness: `SleepDisabled` and system-sleep assertions are reported first with the likely source; `status --json` adds `sleep_ready` and the menu bar shows a warning icon.

### Fixed

- Temporary directories no longer pile up: dry-run keeps the five most recent, demo mode and the e2e/stress scripts remove theirs (also on failure), and the test suite fails if it leaves any `travel-mode-*` directory in the system temp folder. About 7,500 leftovers had accumulated on the development Mac.
- Sleep detection reads `kern.sleeptime`/`kern.waketime` instead of the slow pmset log, which timed out on real hardware; power-setting writes for different domains run one after another (in parallel they raced); result counts, quit reasons and caffeinate sources are shown correctly.
- A retry that succeeded is now marked as recovered even when the action label is long; failures are matched by action type and exact target.
- The session screen no longer reports an internal error in the first minute of a session; it waits for new battery data.
- Session reports and history use local time, readable durations and percentages; failures fixed by a later retry are listed as recovered; commands, exit codes and mAh moved to `--verbose`.
- A short session without power-source events in the pmset log is now counted as battery only when both ends were on battery, instead of "unknown".
- The TUI no longer lets sudo print `Password:` over the screen and time out. Items that need a password are listed first (enter password, skip, cancel); entering it suspends the TUI for `sudo -v`. Items run without permission are reported as "needs administrator password" with a Retry button.
- Apps that answer quit with -128 or time out but exit shortly afterwards are counted as quit; the main process is watched for up to 10 seconds and lingering helpers no longer cause a false failure.
- Starting a scenario while a session is active no longer merges silently: choose restore and start, continue (`--continue-session`) or cancel; `--yes` cancels.
- Health results read as ok / needs attention / cannot check in one sentence; idle-sleep assertions are explained as not blocking lid-close sleep; technical evidence moved to `check --verbose`, JSON and the log.
- Power previews list only settings that change, each with a plain explanation, and fold unchanged ones into one line.
- The menu bar no longer shows macOS's fluctuating remaining-time estimate; during a session it uses the session's average power.
- App rows in a finished state no longer offer retry or force quit.
- An app that cancels the quit request (AppleScript -128, for example a "quit anyway?" dialog) no longer crashes the TUI with a traceback. Quit results are classified; `away` falls back to force quit and reports one warning, `work` marks the item failed and keeps retry and force-quit controls. Other actions in the batch continue, state errors stop further changes, and unexpected errors keep the TUI open with one line and a log path (`TRAVEL_MODE_DEBUG=1` keeps the traceback).
- Report power settings a Mac does not expose (for example `SleepDisabled` on a virtual machine) as unavailable in check, JSON (`unavailable` status) and the TUI instead of failing; e2e records such capabilities as SKIP.
- Publish e2e harness telemetry atomically with a complete schema, including on Python 3.10.
- Describe Low Power Mode as reducing energy use with effects that vary by Mac model, rather than promising CPU/GPU frequency or brightness changes; cite Apple's wording and dated third-party observations in both READMEs.
- Share one structural AppleSmartBattery parser across session endpoints, measurements and the TUI; nested ChargerData keys and other registry nodes cannot overwrite top-level battery status.

- Measure fresh PowerTelemetryData accumulator intervals instead of averaging repeated cached voltage/current reads. Default windows require two fresh updates, label a single interval as lower precision, poll every 5 seconds and time out after 180 seconds without fabricating a result; session averages use endpoint accumulator deltas. Label the voltage/current fallback and update e2e diagnostics and wait guidance.
- Read discharge capacities from either top-level AppleRaw fields or BatteryData, record the selected source, and share capacity parsing with session endpoints. Power command read/parse failures return exit code 4 with a single diagnostic or a JSON error object; debug mode retains the traceback.
- Wait for admitted TUI button transactions and Action threads before removing controls on exit; serialize language recomposition with mutations and use explicit completion signals in TUI tests.
- Pin Apple system tools and sudo targets to fixed macOS paths at execution time; report optional PATH-selected tool locations in check. E2e traces use the same resolver without expanding the guard allowlist.
- Restrict e2e app, agent, assertion and category discovery to fixtures; keep full process observations for safety checks and disable health remediation controls in the UI harness.
- Skip measurement on external power before ioreg sampling; retain auxiliary measurement failures without aborting Actions or the TUI. Record e2e command timing and timeout diagnostics.
- Exercise scenario selection in CLI/TUI demo drivers and verify work/away round trips separately.
- Merge overview status and health presentation; reference repeated process evidence and clarify caffeinate instructions.
- Preserve user LaunchAgent discovery roots while dry-run isolates HOME and write paths; deduplicate repeated unloads in one plan.
- Label dry-run action results as plans, count intended freeze targets and show projected battery values.
- Document language persistence and remove the maintainer command proxy from public test instructions.

### Added

- `travel-mode setup` (TUI and prompts, shown on first run): per-app actions for `work` and `away` with a recommendation and reason for each app, a reviewed summary and an atomic save that keeps comments. Per-scenario `[apps.work]` / `[apps.away]` tables; existing configuration files keep their previous behavior. After a changed action succeeds, the tool offers to remember it.
- Failure log `travel-mode.log` in the state directory (mode 0600, 1 MiB with one rotation) and failed actions in session reports and history.
- A fault-injection test sweep over every mutating command in the CLI and TUI flows, and an e2e fixture app that refuses to quit.

- Explicit sudoers print/install/uninstall/status commands, a pmset-first scope and documented broader operational permissions, with syntax validation, confirmation and Action/Executor mutation routing.
- Cheap bilingual status JSON and a five-minute SwiftBar plugin; scans and measurements run only after menu clicks.
- `menubar install|uninstall|status`: copy the bundled SwiftBar plugin into SwiftBar's plugin folder (`--force` to replace a different file, dry-run preview), remove only our file, and compare the installed version.
- Redesigned menu bar plugin: monochrome SF Symbols with session time, localized session and battery lines, terminal commands that refresh afterwards, hidden SwiftBar default items and a fixed executable search order. `status --json` adds battery percent, remaining time, charging state, elapsed time and app version without extra system calls.
- `e2e_ui.py --demo --demo-vm` replays the hosted macOS runner's recorded capabilities, which lack some power settings.
- A separate macOS e2e workflow for manual, weekly and selected main pushes, with observed VM capability SKIPs and 14-day failure evidence; no change to fixture guards or branch-protection checks.
- A maintainer-only `measure_lowpower.py --plan` and guarded battery 0→1→0 experiment using fresh telemetry accumulator intervals, non-interactive passwordless pmset writes, contextual brightness/CPU observations and verified rollback on success, errors or Ctrl-C.
- Optional power-source LaunchEvents automation with explicit install/uninstall/status, bounded local receipts, notification defaults and opt-in non-interactive safe work/restore actions. Consume XPC events before handling and exit; no resident monitor, periodic launch or lid/sleep trigger.

- Add an opt-in battery-only CLI/TUI e2e mode with a two-process fixture load, measured freeze/restore checks, numerical diagnostics and a stricter guard that rejects all sudo commands. Ordinary real e2e retains its AC requirement.
- Session reports after restore, local retained JSONL history and same-scenario power comparisons, with explicit incomplete charging/log intervals.
- Scrollable bilingual session/history views and a history JSON schema.
- Bounded on-demand health checks shared by check/JSON and the bilingual overview, with evidence, manual steps and existing Action remediation buttons.
- Public keep-awake app recognition and configurable brightness/CPU health thresholds; individual read failures remain visible without stopping other checks.
- On-demand battery power measurement, configurable finite windows, work before/after comparisons and saved session results.
- Open-TUI battery readings every 30 seconds, button-only energy ranking and one-shot resurrection detection with existing Action remediation.
- A read-only resource.getrusage overhead measurement script and battery/check JSON schema coverage.
- Work and away scenarios, a bilingual startup picker, CLI scenario selection, configuration and snapshot/JSON scenario fields.
- Packaged public category rules for sync, backup, updater and VM products; grouped discovery, category pause controls and explicit keep/skip precedence.
- Single-line configuration/state diagnostics with exit codes 2/3 and opt-in debug tracebacks.

### Changed

- Work defaults change battery Low Power Mode only, preserve network/display/AC settings and leave unconfigured foreground apps alone.
- Rewrite both READMEs around the two use cases, action boundaries, recovery and verification limits.

### Removed

- Merge the cafe profile into work; remove cafe without a compatibility alias.

## [1.1.0] - 2026-10-15

### Added

- English and Chinese terminal dashboards with five pages, keyboard/mouse navigation, immediate language switching and comment-preserving configuration writes.
- Power-setting explanations, values and trade-offs; built-in flight, overnight and cafe profiles; supported-key detection including equivalent power-mode keys.
- Read-only energy suggestions that discover app daemons, explain matching confidence, report malformed plists and optionally write confirmed configuration entries.
- JSON reports and schemas, XDG configuration/state paths, extra app search directories, SwiftBar and Shortcuts integration, and deterministic demo screenshots.
- Isolated CLI/TUI end-to-end harnesses with explicit skip reasons, recovery checks and a separately guarded native brightness round trip.

### Changed

- Request sudo credentials only before a command that needs them. Check permission for the exact command, keep credentials for the active operation and use non-interactive sudo for privileged observations.
- Share actions and recovery rules across CLI and TUI. Separate system adapters, flows and page presenters so both interfaces apply the same safeguards.
- Reuse app/process discovery within a refresh, load pages independently, reject stale refresh results and retain missing/stopped inventory rows with explanations.
- Show configured service matches, invalid plist diagnostics and read-only Time Machine status. Keep system-managed backup services out of unload targets.
- Make hibernatemode 25 an independent TUI selection when supported, including when the current value already matches the ordinary recommendation.
- Install from Git with uv or pipx for the initial public release; mark PyPI and Homebrew distribution as not yet published.

### Fixed

- Restore only power keys actually changed, in the original battery or AC domain. Keep the first original value across repeated actions and leave unrelated settings alone.
- Persist recovery intent before mutation and checkpoint each successful restore. Keep failed or uncertain records for retry instead of reporting success or deleting recovery data.
- Freeze, resume and terminate exact process identities after checking birth time and executable; protect the terminal, ancestor processes and system apps. Resume only recorded processes during normal restore.
- Limit caffeinate termination to verified sleep-assertion holders, show full command/parent details and confirm each PID independently.
- Validate every daemon plist and selector before applying actions, match exact service labels during restore and escape application identifiers in AppleScript.
- Isolate dry-run configuration, migration, language and recovery writes. Preserve uncertainty and return failure when required privileged observations cannot be read.
- Serialize TUI mutation transactions, reject duplicate queued clicks, preserve row identity across refresh/recompose and avoid updating detached controls.
- Surface failed commands, invalid state and unsupported recovery operations explicitly; avoid resetting factory power defaults without separate interactive confirmation when no snapshot exists.

### Removed

- Machine-specific configuration examples and internal development/verification logs from public files and distribution archives.

## [1.0.0] - 2026-10-11

Initial version.
