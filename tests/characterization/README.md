# Characterization contracts

Run with `uv run pytest tests/characterization`. All commands use a strict
FakeRunner, fixture bundles/plists and isolated config/state/HOME. No test in
this directory changes the host system. Locale, timezone, executable discovery,
platform checks and clocks are explicit fixture inputs.

## Golden boundary

Each golden has exactly three fields:

- `commands`: the complete ordered logical argv trace, including reads and sudo probes;
- `state`: the complete final persisted state, or `null` after successful/forced cleanup;
- `exit_code`: the CLI code, or `null` for a TUI action without a process exit.

Only the fixture root, fixed wall clock and interpreter path are normalized.
State includes all recovery intentions, exact PID receipts and failure records;
none of its fields are projected out. In particular, a failure reason persisted
in state remains part of the complete state, even though presentation output is
no longer snapshotted.

Console output, UI logs/messages, row labels, row rendering, view names, refresh
counts and TOML source text are not golden fields. Text and visual changes need
bilingual screenshot review. Separate structural assertions preserve semantics:

| Removed snapshot content | Semantic assertions kept |
| --- | --- |
| Config source/template text | `test_cli_contract.py`: parsed TOML before/after, full parsed shipped template for init, language change only; `test_interface.py` retains interface priority and atomic-write coverage |
| Public JSON serialized as output | `test_cli_contract.py`: parse actual CLI output, check/suggest public schemas, battery/source, blocker PID/type, power current/target/capabilities, app actions, service selection, nine health outcomes, sleep intervals and wake causes |
| Restore messages and result tuple | `test_restore_branches.py`: exact restored/failed counts, invalid-plist failure records and remaining recovery entries |
| Partial/forced off output | `test_cli_contract.py`: failure kind/rc/command/target, retained Bluetooth receipt, retry does not repeat completed restores, archived failure/recovery and final state removal; force keeps an unresolved failure in its report |
| TUI rows/logs/view names | `test_tui_contract.py`: selected rows finish, unselected rows skip, terminal rows have no retry/force controls, thaw clears PID receipts, off removes state, language is persisted; failure categories and retry controls are checked in `test_fault_sweep.py` and the dedicated lifecycle/admission tests |

Sixty scenarios still execute. `contracts.py::ALIASES` maps thirteen scenarios
to identical safety expectations, leaving 47 JSON files. The initial migration
only projected existing reviewed contracts and proved equality before sharing:

| Shared expected file | Additional scenarios |
| --- | --- |
| cli-config-init---force | cli-config-set-language-zh, config-init-new |
| cli-config-path | cli-config-validate |
| cli-report---json | cli-report |
| cli-suggest---json | cli-suggest |
| cli-thaw---all | thaw-accept |
| cli-thaw | thaw-reject |
| off-complete | off-force |
| tui-daemon | tui-network-daemon |
| tui-dashboard-en | tui-dashboard-zh, tui-language, tui-skip |
| tui-service-spotlight | tui-spotlight |

Sharing does not equate scenario preconditions. For example, ordinary off and
force off retain separate failure/report assertions, and each config mutation
has its own parsed-config assertion. If one scenario intentionally diverges,
split its alias and review its own contract; do not change the other scenario's
expectation to hide the difference.

## Fault coverage by layer

`test_fault_sweep.py` keeps the real CLI/flow and Executor/plan boundaries.
`fault_support.py` restores a sealed machine before each injection. The plan
tests use production `collect`, `execution_units`, credential preflight and
Executor, without starting Textual or Pilot. Every injection is followed by off
and checks that recovery finishes. State/internal errors must propagate and stop
later mutations; ordinary command/password failures must permit independent work.

| Fault family × path | Before | After |
| --- | --- | --- |
| rc=1 command failure | CLI work/away × safe/aggressive; repeated Pilot work/away × quick/apps/services/network | Same four CLI sweeps; same eight plans at Executor boundary, all 42 modification occurrences |
| AppleEvent -128 refusal | Extra injection at every osascript occurrence in those CLI/Pilot paths | Same CLI coverage; all 12 plan osascript occurrences, including fallback outcomes |
| Password not provided | Every privileged CLI command and Pilot quick work/away | Same CLI command enumeration; all 16 privileged occurrences across eight plans, rc77/not_executed and no sudo-v |
| State save failure | Separate before/after-write examples; no full Pilot sweep | Real save failure at each of the 42 plan modification boundaries; original StateError, stopping and off recovery |
| Unexpected exception | Separate Action/worker examples | Runner LookupError at each of the 42 plan modification occurrences; internal log, original exception, stopping and off recovery |
| Real UI admission/continue/retry/off | Repeated per-command Pilot startup | Five Pilot representatives: command error, refusal, password dialog skip, state write failure, internal error; real retry and session/off buttons |

The measured original Pilot and new Executor drivers had identical complete
ordered mutation argv for all eight paths, not just equal counts. Command
families are battery/AC pmset, osascript quit, kill STOP, user/system launchctl
unload, networksetup, blueutil, mdutil and brightness. CLI additionally covers
global power commands, force quit and display sleep. Off exercises CONT, load
and open as applicable; restore-branch contracts protect their detailed order.
Existing PID safety, journal, lifecycle, duplicate-click, scenario-admission and
before/after-write regressions remain independent tests.

## Reviewing intentional changes

A mismatch writes a complete safety-only artifact for the individual scenario
to `/tmp/tm-verify/golden-actual`. Expected files are never overwritten by a test.
Review command order, full state, recovery receipts and exit codes, then run:

```bash
uv run python tests/characterization/update_golden.py /path/to/reviewed-artifacts --acknowledge-behavior-change
```

The updater validates every artifact before writing any file. Unknown names,
missing/extra fields (including display text), invalid argv/state/code and
conflicting alias artifacts fail explicitly. Alias artifacts address the shared
file, but contradictory projections require splitting the alias first.

`TRAVEL_MODE_RECORD_INITIAL=1 uv run pytest tests/characterization` records only
missing, untracked canonical contracts. It cannot overwrite or recreate a
tracked expected file. Historical checksums do not replace current review.

TUI contracts finish initial sampling before recording actions and suppress
subsequent sampling requests. Pilot/lifecycle tests independently exercise real
refresh workers and committed layouts. TUI/async changes can be repeated locally
with `uv run python scripts/stress_tui.py --runs 10 <test ids>`.

For a Linux product-platform/font simulation on macOS, use an isolated HOME:

```bash
mkdir -p /tmp/travel-mode-ci-home
env -i PATH="$PATH" HOME=/tmp/travel-mode-ci-home CI=true \
  COLUMNS=57 LANG=C.UTF-8 TZ=Pacific/Honolulu PYTHONPATH=tests \
  uv run --no-sync pytest -p ci_host </dev/null
```

`ci_host` changes only the product platform view and rejects macOS font paths;
the interpreter platform remains native for event-loop/extensions.
