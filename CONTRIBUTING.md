# Contributing

## Development

Use the repository-managed Python toolchain:

```bash
uv sync --group dev
uv run ruff check
uv run ruff format --check
uv run pytest --cov=travel_mode --cov-report=term-missing
uv build
uv run python scripts/check_dist.py
```

Run `uv run pre-commit install` if you want local commit hooks. The default pytest
selection excludes `macos` and `e2e`. `uv run pytest -m macos` selects the read-only
macOS smoke test; it still isolates all user paths. Linux tests mock macOS commands
and do not imply Linux runtime support.

Tests use the strict `FakeRunner` in `tests/conftest.py`. Add explicit command
responses and assert the recorded calls. Never execute a real state-changing
command in an ordinary test. Config, state, HOME and XDG roots must be temporary;
per-test and session guards check that real user config/state stay unchanged.

User-visible messages belong in both `src/travel_mode/locales/en.toml` and
`zh.toml`. Keep key sets and placeholders identical. After adding a message, run
`uv run pytest tests/unit/test_i18n.py tests/unit/test_no_cjk_literals.py`.
The privacy guard checks public files of every extension; review screenshot pixels
separately before publishing. Use Conventional Commits for proposed changes and
include validation and hardware limitations in the pull request.

## Architecture and new actions

`cli.py` parses arguments. `flows/` owns prompts, reports, config commands and
checkpointed restore. `snapshots.py` captures originals; `state.py` validates and
atomically saves the versioned recovery format.

`actions/` is the shared CLI/TUI mutation boundary. Each Action describes an
operation, declares its risk and implements `apply(ctx)`. Executor owns credential
lifetimes, ordering and admission of one TUI mutation transaction at a time.
The journal persists recovery intent before the Runner receives a mutation.

`system/` owns macOS adapters. `process_control.py` validates PID birth time and
executable identity and protects Terminal, ancestors and system apps.
`daemon_apps.py` validates every configured plist before unloading any daemon.
The check immediately before a signal reduces PID-reuse risk but is not atomic
with the kernel signal operation.

`tui/app.py` owns TravelApp and refresh lifecycle. `layout.py` composes pages,
`dispatch.py` admits button messages, and `pages/` selects Actions and presents
results. Widgets live in `widgets.py`; CSS stays in `style.tcss`.

To add an Action:

1. Define its recovery record and restore operation first. Extend state validation
   and `actions.journal.prepare_command` for any new mutation command. Persist
   intent before mutation; retain uncertain failures.
2. Add the Action, an i18n description and `safe` or `aggressive` risk. Route all
   mutations through the appropriate system adapter.
3. Use `Executor(Context(snapshot, save)).execute(action)` in independent flows.
   CLI apply supplies the journal scope. TUI presenters use `self.execute(action)`
   inside the admitted transaction. Do not send mutation commands directly from a
   presenter or duplicate PID/plist policy.
4. Add strict FakeRunner tests for ordering, receipt persistence before mutation,
   partial failure and recovery. Cover CLI and Pilot entrypoints where applicable.
5. Run lint, format and the default test suite. Keep characterization contracts
   unchanged for structural changes; review intentional behavior changes using
   [the characterization guide](tests/characterization/README.md).

## Isolated TUI checks

```bash
uv run python scripts/tui_debug.py --all
```

This defaults to demo mode. It writes PNG, text, ANSI, layout telemetry and
`lint.json` under `/tmp/tui-debug/<run-id>/`. It covers five tabs, both languages
and 120x36, 80x24 and 160x45 terminals. `--keys`, `--click X Y` and `--wait-text TEXT`
select focused interactions. `--live-readonly` observes the host with isolated
HOME/XDG/config/state; its Runner rejects mutations and every sudo command.

```bash
uv run python scripts/tmux_capture.py --demo
```

This regenerates checked-in bilingual demo assets through the shared cell-grid
renderer. Never use real user config/state as output paths. Screenshot/debug
commands require tmux and the macOS fonts used by `scripts/terminal_render.py`.

## End-to-end harness

Automated development runs use FakeRunner, isolated demo or command-free plans:

```bash
uv run python scripts/e2e_local.py --plan
uv run python scripts/e2e_ui.py --plan
uv run python scripts/e2e_ui.py --demo
uv run python scripts/e2e_ui.py --battery-measure --plan
uv run python scripts/e2e_ui.py --battery-measure --demo
```

The demo runs real CLI questions, CLI off and TravelApp against the stateful
`e2e_fake.Machine`. HOME, XDG, config/state and tmux socket are isolated. Prompts
must match complete i18n output before answering. TUI navigation uses keys and
mouse and waits for completed action generations. Evidence and explicit failure
reports remain under `/tmp/tui-e2e/<run-id>/`. Select `--interface cli|tui` for a
focused demo, or `--output PATH` for a new evidence directory.

**Only a maintainer who has reviewed the scripts should opt into real mode:**

```bash
uv run python scripts/e2e_local.py
uv run python scripts/e2e_ui.py
```

These commands change the system. They require AC power, refuse existing real
state or conflicting fixture agent labels, and restrict mutations to temporary
fixture apps/agents and selected power keys. GuardRunner verifies exact argv and
PID identities and rejects interactive authentication. Power commands map to
`sudo -n /usr/bin/pmset`; missing permission skips power with a reason.

An optional sudoers example uses a placeholder, not a real account:

```sudoers
<your-username> ALL=(root) NOPASSWD: /usr/bin/pmset
```

This grants all pmset operations, including disruptive ones. Prefer exact argv
rules covering both selected writes and their captured rollback commands; see
[the sudoers guide](docs/sudoers.md). Product authentication first checks the exact
command with `sudo -n -l <command>`; no blanket `sudo -v` exception is needed.

The harness covers fixture quit/freeze/reopen, user LaunchAgent unload/reload,
AppleEvent graceful quit, battery keys including hibernatemode 25, global
`disablesleep`, AC `powernap`/`womp`, CLI thaw and a separate brightness round trip.
The UI selects hibernatemode 25 independently even when current/recommended=3.

Brightness samples the original through native DisplayServices Get. Initial Get
failure is an explicit SKIP. Normal stages reject native writes. The isolated
brightness stage permits only 0.15 and the sampled original; it verifies target
error ≤0.02 and off-restored error ≤0.01, without screenshots or deliberate waits
while dimmed. Exceptions and Ctrl-C restore and verify brightness first. Native
calls bypass subprocess, so this restriction is enforced separately from
GuardRunner. Demo injects a simulated backend and never loads system frameworks.

Real applets use an isolated bundle ID, hidden background reopen, exact AppleEvent
argv, and final unregister/cleanup. First-run Automation denial or timeout is a
SKIP, not a pass. Cleanup verifies identity before signals. Power originals and
rollback permissions are captured before mutation; cleanup restores and compares
complete power output and `SleepDisabled`. `thaw --all` skips if any foreign app is
already stopped. Before/after process snapshots only cover sampling instants.

The real harness deliberately excludes Wi-Fi, Bluetooth, Spotlight, immediate
screen-off, terminating user `caffeinate` processes and AC sleep/displaysleep.
They must not be inferred from demo results or default pytest success. The current
hardware verification scope is listed in both READMEs.

### Battery measurement with fixture load

For a reviewed, maintainer-run test while unplugged, use
`uv run python scripts/e2e_ui.py --battery-measure` (`--interface cli|tui`
selects one interface). This mode requires a measurable, discharging battery and
refuses AC before starting fixtures. Ordinary real e2e still requires AC.
It changes only the isolated fixture apps and user LaunchAgent. It does not
change power settings, brightness, networking or the display. Its guard rejects
all sudo commands, including permission probes, independently of the UI policy.
CLI power, AC, disablesleep, hibernatemode, brightness and display-off answers are
no; the TUI runs the measured work quick action with only apps and services
selected. Native brightness setters remain inaccessible. No power or brightness
round trip or emergency power rollback is scheduled.

An additional Burn app uses private copies of `sh` for its main and helper,
each executing `while :; do :; done`. The two processes can occupy about two CPU
cores. They warm up for 5 seconds; the product waits for three fresh telemetry
updates covering two accumulator intervals (5-second polls, 240-second timeout),
freezes them through its existing Action, settles for 10 seconds, then measures
again with the same policy. The cached first read is not an accepted baseline.
Allow up to 490 seconds for both windows and settling per interface, plus fixture setup
and checks. Both default
interfaces run sequentially. Off must resume the same PID/birth identities before
the existing identity-checked cleanup terminates the fixtures.

The run requires valid before/after measurements with a recorded capacity source,
at least 1.0 W measured saving, real mAh session endpoints, average power and a
successful restore. Two busy interpreters provide controlled load; the 1 W
threshold is a conservative harness acceptance criterion, not a hardware power
guarantee. Background work and thermal changes can invalidate that criterion.
On failure, `result.json` and terminal diagnostics retain both window averages
and numerical ioreg samples, without raw battery identifiers. Each accepted interval
prints its average power; duplicate generations never inflate sample counts.
Insufficient updates, counter resets/wraps or a changed source invalidate the run. Observed external
power or incomplete battery-source log coverage invalidates the run; no discharge
values are synthesized. The log check covers preflight through off, including
warmup and the first measurement before the first Action starts a session.
This is one finite test, with no resident monitor.

电池测量模式只操作临时 fixture，不修改电源、亮度、网络或显示器设置。
它要求拔电且正在放电；接电时拒绝启动。双进程 CPU fixture 会额外耗电，
每个界面的两个窗口与稳定期最多共需 490 秒，另需准备 fixture 与检查；两个窗口各等待至少 3 次真实遥测更新。样本不足时判为无效。普通真机 e2e 仍要求接电。运行前先查看
`--battery-measure --plan`；自动化开发只运行 `--demo`，真机模式由维护者运行。

### Event automation validation

`uv run python scripts/e2e_automation.py --plan` lists the complete sequence. Only a maintainer runs the command without `--plan`: it installs the independent `io.github.yulong-ge.travel-mode.events-e2e` label with a temporary guarded Python helper and isolated config/state. Both policies are `off`, so a real event only records the observed source. The helper rejects notifications, authentication and mutations; the parent guard only permits print/bootstrap/bootout for that exact test label and plist, and validates the complete plist before bootstrap. It also reads the current power source before and after the manual transition.

Change AC/battery source manually, allow the handler to finish, then press Enter in the harness. It checks the local receipt and `launchctl print` for a non-running state, then always boots out that label and removes its plist. It refuses an existing test plist. Evidence remains in the printed temporary directory. It does not post synthetic power notifications and does not modify the production automation agent. This delivery check does not validate the opt-in power/app mutations; those use existing Action tests and the separate fixture e2e harness.
