# Changelog

All notable changes follow Keep a Changelog.

## [Unreleased]

### Fixed

- Preserve user LaunchAgent discovery roots while dry-run isolates HOME and write paths; deduplicate repeated unloads in one plan.
- Label dry-run action results as plans, count intended freeze targets and show projected battery values.
- Document language persistence and remove the maintainer command proxy from public test instructions.

### Added

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
