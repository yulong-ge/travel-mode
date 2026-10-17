# Changelog

All notable changes follow Keep a Changelog.

## [Unreleased]

### Fixed

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
