# Contributing

Thanks for helping. This guide covers the development setup, the rules the test suite enforces,
and which scripts change your system. Architecture notes for agents and humans are in
[AGENTS.md](AGENTS.md); detailed product behavior is in [docs/reference.md](docs/reference.md).

## Setup and checks

```bash
uv sync --group dev
uv run ruff check && uv run ruff format --check
uv run pytest                                        # default suite (excludes `macos` and `e2e`)
uv run pytest tests/unit/test_config.py::test_name   # one test
uv build && uv run python scripts/check_dist.py      # package contents
```

CI runs the suite on Python 3.10–3.13 on macOS.
`uv run --python 3.10 pytest` catches syntax or library features newer than 3.10.
`uv run pre-commit install` adds local hooks. Use Conventional Commits, and describe how you
validated the change (and what hardware it was not tested on) in the pull request.

## Test rules

- **No real system changes.** Tests run with a strict `FakeRunner` (`tests/conftest.py`) that
  rejects any command you did not register and any host subprocess. Register responses and assert
  the recorded calls.
- **No real user files.** Config, state, HOME and XDG roots are temporary; guards fail a test that
  touches the real `~/.config/travel-mode` or `~/.local/state/travel-mode`.
- **Hermetic.** Do not depend on the host's sudoers, fonts, locale, terminal size, installed apps or
  `sys.platform`. Tests that reach macOS-only entry points use the `darwin_platform` fixture.
- **Behavior contracts.** `tests/characterization/` records the commands, final state, output and
  exit code of each flow. A mismatch means behavior changed: investigate it. Update a contract only
  for an intended change, through `tests/characterization/update_golden.py`
  ([guide](tests/characterization/README.md)), and explain it in the pull request.
- **Both languages.** Every user-visible string goes into `src/travel_mode/locales/en.toml` and
  `zh.toml` with the same keys and placeholders (`tests/unit/test_i18n.py`).
- **No personal data.** `tests/unit/test_no_personal_terms.py` scans public files for private
  identifiers. Screenshots are checked by eye; regenerate them in demo mode.
- **Stable async tests.** New or changed TUI/async tests must pass repeatedly under load before
  they are committed:
  `uv run python scripts/stress_tui.py --jobs 10 --runs 30 <test ids>`.

## Adding an Action

Every change to the system goes through an Action, so the CLI and TUI share the same safety
checks and recovery records.

1. Define the recovery record and the restore step first. Extend state validation and
   `actions.journal.prepare_command` for the new command; recovery intent is saved before the
   command runs.
2. Add the Action with an i18n description and a `safe` or `aggressive` risk, and run its commands
   through the matching `system/` adapter (never directly from a flow or page).
3. Run it with `Executor(Context(snapshot, save)).execute(action)`; TUI pages call
   `self.execute(action)` inside the admitted transaction.
4. Test ordering, that the record is saved before the command, partial failure and restore, from
   both the CLI and the TUI where it applies.

## TUI screenshots and debugging

```bash
uv run python scripts/tui_debug.py --all            # every tab, both languages, three sizes
uv run python scripts/tui_debug.py --live-readonly  # real machine data, mutations refused
uv run python scripts/tmux_capture.py --demo        # regenerate docs/assets screenshots
```

Output goes to `/tmp/tui-debug/<run-id>/` (PNG, text, layout telemetry and `lint.json`). These
need tmux and the macOS fonts used by `scripts/terminal_render.py`. `--live-readonly` uses a copy of
your configuration and an isolated HOME; its runner rejects every write and every sudo call.

## End-to-end tests

The e2e scripts drive the real CLI and TUI. A guard runner allows only exact, pre-approved
commands against temporary fixture apps and agents, verifies process identity before any signal,
and restores and compares the original state on success, failure or Ctrl-C.

| Command | Changes the system | Who runs it |
| --- | --- | --- |
| `e2e_local.py --plan`, `e2e_ui.py --plan`, `--battery-measure --plan` | no (prints commands) | anyone |
| `e2e_ui.py --demo`, `--demo --demo-vm`, `--battery-measure --demo` | no (fake machine) | anyone, CI |
| `e2e_local.py` | yes: fixtures, battery and AC power keys, sleep repair, brightness; needs AC power | maintainer, after reading the plan |
| `e2e_ui.py` | yes: same scope through the CLI and TUI; needs AC power | maintainer |
| `e2e_ui.py --battery-measure` | fixtures only, no power settings; needs battery; runs a busy test app for a few minutes | maintainer |
| `e2e_automation.py` | installs and removes a separate test LaunchAgent; you switch the power source once | maintainer |
| `measure_lowpower.py` | battery `lowpowermode` only, restored afterwards; needs battery | maintainer |

All scripts live in `scripts/`. Before changing anything, the real modes refuse to start when they
find an active travel-mode session or a leftover test agent from an earlier run. Power
steps use `sudo -n /usr/bin/pmset` and are skipped, with the reason, when passwordless permission is
missing; they never prompt. Wi-Fi, Bluetooth, Spotlight, display sleep, terminating a user's
`caffeinate` and AC sleep timers are deliberately not exercised.

`--demo-vm` rehearses a machine without a battery profile, adjustable brightness or AppleEvent
permission. Evidence is written to `/tmp/tui-e2e/<run-id>/` and `/tmp/travel-mode-e2e-evidence/`.

### CI on a macOS runner

[`.github/workflows/e2e-macos.yml`](.github/workflows/e2e-macos.yml) runs `e2e_local.py` and
`e2e_ui.py` on a hosted macOS VM: weekly, on pushes to `main` that touch the product or harness,
and on demand (Actions → **macOS guarded e2e** → **Run workflow**). It is not a required check.
The VM has no battery or adjustable display, so those steps are reported as SKIP; its passwordless
sudo does not widen the guard. Screenshots, results and command traces are uploaded as the
`macos-e2e-<run-id>-<attempt>` artifact for 14 days. Background: [runner notes](docs/runner-e2e-research.md).

## Documentation

User documentation is `README.md`, `README.zh-CN.md` and `docs/`. Keep both READMEs in step, describe
what the tool does in plain language, and name the machine for any measured number.
