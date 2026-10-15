# Characterization contracts

Run with `uv run pytest tests/characterization`.

The fixture uses isolated app bundles, plists, config, state and a strict
FakeRunner. Contracts store ordered command traces, final JSON state,
configuration, exit codes and user-visible output. Only the isolated root and
fixed clock are normalized. TUI sampling completes before recording; subsequent
refresh requests are recorded without concurrent sampling workers. The Pilot
rendering suite covers those workers and their page lifecycle.

The shared conftest pins locale, timezone, language, terminal probes, executable
availability and application discovery. Privileged probes (including sudo
permission checks) stay inside FakeRunner; ordinary tests reject host subprocesses.
The machine and local-harness fixtures explicitly model Darwin at the product's
platform boundary. Parser tests and the non-macOS rejection test still run on
Linux. The renderer test supplies Pillow's embedded font; production rendering
still fails if its requested macOS font is missing.

Rich wraps paths before they are normalized to `<ROOT>`. `contract_root` creates
a unique, temporary path with an 87-column parent and a deterministic test-name
suffix, preserving the existing goldens' wrapping without depending on TMPDIR,
the pytest session counter or the user's name. All golden fields and comparisons
remain unchanged.

The interface option extends the config-init template. Those two cases assert
the exact new comment and `interface = "tui"` entry, then compare the original
template projection against the unchanged goldens. `test_interface.py` also
validates both complete templates and the saved interface. No command, state,
exit-code or output fields are projected out.

The `apply-all`, `apply-none` and `apply-mixed` goldens add only the first-line
CLI prompt guide. Non-interactive `--yes` contracts keep their original output.

For a Linux platform/font boundary simulation on macOS, run from the repo root:

```bash
mkdir -p /tmp/travel-mode-ci-home
env -i PATH="$PATH" HOME=/tmp/travel-mode-ci-home CI=true \
  COLUMNS=57 LANG=C.UTF-8 TZ=Pacific/Honolulu PYTHONPATH=tests \
  uv run --no-sync pytest -p ci_host </dev/null
```

`ci_host` changes only the product's `sys.platform` view to Linux and rejects
macOS font paths. It leaves the interpreter's platform unchanged so third-party
event-loop and native-extension selection still match the actual interpreter.

`TRAVEL_MODE_RECORD_INITIAL=1 uv run pytest tests/characterization` records only
missing, untracked contracts. It cannot overwrite existing files or recreate a
tracked file. A mismatch needs investigation; do not regenerate expectations to
hide a regression.

Failed comparisons leave diagnostic JSON in `/tmp/tm-verify/golden-actual`.
After reviewing an intentional behavior change, use:

```bash
uv run python tests/characterization/update_golden.py /path/to/reviewed-artifacts --acknowledge-behavior-change
```

Review the complete diff, including command order, recovery receipts, output and
exit codes. This command only updates known, complete contracts. Historical
checksums are not a substitute for reviewing current behavior.
