# AGENTS.md

Guidance for coding agents working in this repository. Human-oriented details live in
[CONTRIBUTING.md](CONTRIBUTING.md) and [tests/characterization/README.md](tests/characterization/README.md).

travel-mode is a macOS battery-saving tool (Python 3.10–3.13, Textual TUI + CLI). It snapshots
the current state, applies power/app/launchd/network changes, and `travel-mode off` restores
exactly what was changed. Runtime is macOS-only; the test suite runs on macOS and Linux.

## Commands

```bash
uv sync --group dev                      # environment (always use uv, never system python/pip)
uv run ruff check && uv run ruff format --check
uv run pytest                            # default suite; excludes the `macos` and `e2e` markers
mkdir -p /tmp/travel-mode-ci-home && env -i PATH="$PATH" HOME=/tmp/travel-mode-ci-home CI=true COLUMNS=57 LANG=C.UTF-8 TZ=Pacific/Honolulu PYTHONPATH=tests uv run --no-sync pytest -p ci_host </dev/null # run before committing; CI includes Ubuntu
uv run pytest tests/unit/test_config.py::test_name    # single test
uv run pytest tests/characterization     # behavior contracts (golden files)
uv run --python 3.10 pytest              # CI runs 3.10–3.13 on ubuntu and macOS
uv build && uv run python scripts/check_dist.py       # package + wheel/sdist content check

uv run travel-mode check                 # read-only audit of this machine
uv run travel-mode --dry-run --scenario away --yes       # prints planned commands, changes nothing
uv run travel-mode --demo               # TUI on fixture data (hidden flag)

uv run python scripts/tui_debug.py --all # tmux screenshots + lint of every tab, both languages, 3 sizes
uv run python scripts/e2e_ui.py --demo   # CLI/TUI round trip against a fake machine
uv run python scripts/e2e_local.py --plan
```

Screenshots land in `/tmp/tui-debug/<run-id>/`; inspect the PNGs instead of asking a human to look
at the TUI. `scripts/e2e_local.py` / `scripts/e2e_ui.py` without `--plan`/`--demo` change the real
system; only a maintainer who has reviewed them runs those.

`scripts/e2e_isolation.py` generates fixture-only configuration and projects product discovery
reads onto fixture bundles/PIDs/agents. GuardRunner keeps complete process observations for safety;
the UI harness disables health remediation controls. Do not replace full safety observations with
the filtered product view.

## Hard rules

- Never run state-changing system commands while developing or testing: `sudo` writes, `pmset`
  settings, `launchctl load/unload`, `mdutil -i`, `networksetup -set*`, `blueutil -p`,
  `kill`/`pkill` signals, `osascript ... quit`. Read-only probes (`pmset -g ...`, `launchctl list`,
  `ps`, `ioreg`) are fine.
- Never read or write the real `~/.config/travel-mode` or `~/.local/state/travel-mode`. Use
  `TRAVEL_MODE_CONFIG`, `TRAVEL_MODE_STATE` (and `TRAVEL_MODE_APP_DIRS`) pointing at temp paths.
- Tests are hermetic. An autouse strict `FakeRunner` (`tests/conftest.py`) rejects unregistered
  commands and host subprocesses; register responses and assert the recorded calls. Do not depend
  on the host's sudoers, fonts, locale, terminal width, platform or installed apps.
- Run new or modified TUI/asynchronous tests repeatedly to verify stability before committing.
  Run at least 30 consecutive suite iterations under CPU load: `uv run python scripts/stress_tui.py --runs 30 --coverage` (defaults to one concurrent pytest process per CPU; all processes exit after the finite run). Wait for button completion, current refresh generation and committed layout; worker snapshots and fixed sleeps do not establish action completion.
- Characterization goldens record ordered commands, final state, output and exit codes. A mismatch
  means behavior changed: investigate, don't regenerate to make it pass. Intentional changes go
  through `tests/characterization/update_golden.py ... --acknowledge-behavior-change`, listed and
  justified in the change.
- Every user-visible string goes into both `src/travel_mode/locales/en.toml` and `zh.toml` with
  identical keys and placeholders (`tests/unit/test_i18n.py`, `test_no_cjk_literals.py` enforce it).
- Fail explicitly: no swallowed exceptions, synthesized defaults or silent fallbacks. Config and
  state problems raise `ConfigError` / `StateError` with the file and key path.
- No personal machine identifiers (usernames, hostnames, private app/service lists, temp-path
  prefixes) in committed files; a privacy test guards this. Regenerate published screenshots with
  `scripts/tmux_capture.py --demo`.

## Product principles

- The tool must itself save power: no resident background services, daemons or polling loops.
  Measurements happen only while the user has the tool open; any automation must be event-driven.
- Pausing launchd services uses unload/reload, never `launchctl disable`.
- Docs and UI text state what the tool does in plain language; no rhetorical questions or hype.

## Architecture

Mutation path, shared by CLI and TUI:

```
cli.py → flows/ (apply, restore, check, report, suggest, configuration, thaw)
       → actions/ (Action + Executor + journal)  → system/ adapters → system/shell.py Runner
```

- `system/shell.py` is the only place that spawns processes. The Runner is swappable:
  `FakeRunner` in tests, a dry-run runner that executes only allowlisted read-only commands and
  records the rest, and the guarded runner used by the e2e scripts.
  The real subprocess boundary pins Apple tools and sudo targets to system paths; optional third-party tools use PATH, while logical Runner/journal/dry-run argv stay unchanged.
- `actions/`: each Action declares a risk (`safe` / `aggressive`) and implements `apply(ctx)`. The
  journal persists recovery intent to state *before* the mutation runs; the Executor owns sudo
  credentials (probed per command with `sudo -n -l`, requested only when needed), ordering, and the
  TUI's one-transaction-at-a-time lock. Never send a mutation command directly from a flow or page.
- `snapshots.py` captures original values; `state.py` validates and atomically writes the versioned
  state file; `flows/restore.py` restores only recorded changes and checkpoints each success, so a
  partially failed `off` can be re-run.
- Process targeting is by exact PID with birth-time and executable checks
  (`system/process_control.py`), with Terminal, ancestors and system apps protected. Configured
  daemon plists are validated before anything is unloaded (`system/daemon_apps.py`).
- `config.py` loads strict TOML from XDG paths (`config_template.toml` is the shipped template);
  `profiles.py` holds built-in profiles; `constants.py` holds generic default app lists.
- `system/battery.py` reads ioreg without sudo; `measurement.py` uses finite windows. Only the open TUI owns a 30-second ioreg timer, stopped on exit; energy ranking is button-only, with no resident monitor.
- `system/health.py` collects nine structured read-only checks once per command/open/explicit refresh (2 s per check, 15 s command budget); `health_native.py` isolates native reads under subprocess timeouts. `flows/doctor.py` maps findings to existing Actions; health has no timer or resident monitor.
- `sessions.py` records action counts and one-shot battery observations, seals restore reports before deleting state, and atomically retains local JSONL history. `health_view.py` deduplicates presentation while preserving raw observations. No session sampler runs in the background.
- Two scenarios select the profile and defaults: `work` (unplugged work: screen and network stay
  on, sync/backup/updater programs are paused) and `away` (lid closed / overnight).
- `categories.toml` + `categories.py` are the built-in rules that recognise sync, backup, updater
  and VM programs by public bundle id, launchd label, app name or executable path. Rules must use
  public product identifiers only, each with a source comment.
- TUI: `tui/app.py` (TravelApp, refresh worker, language switch), `layout.py`, `dispatch.py`
  (admits button events), `lifecycle.py` (input completion and draining Action threads before
  teardown), `pages/` (render and choose Actions), `style.tcss`. A refresh builds one
  `system/app_snapshot.py` snapshot (app index + a single `ps`) and redraws each page atomically.

## Repository workflow

- Conventional Commits. `main` is protected (no force pushes; lint, build and the 3.10–3.13
  ubuntu/macOS test matrix must pass).
- Update `CHANGELOG.md` for user-visible changes.
