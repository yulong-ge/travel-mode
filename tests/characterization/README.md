# Characterization contracts

Run with `uv run pytest tests/characterization`.

The fixture uses isolated app bundles, plists, config, state and a strict
FakeRunner. Contracts store ordered command traces, final JSON state,
configuration, exit codes and user-visible output. Only the isolated root and
fixed clock are normalized. TUI sampling completes before recording; subsequent
refresh requests are recorded without concurrent sampling workers. The Pilot
rendering suite covers those workers and their page lifecycle.

`TRAVEL_MODE_RECORD_INITIAL=1 uv run pytest tests/characterization` records only
missing, untracked contracts. It cannot overwrite existing files or recreate a
tracked file. A mismatch needs investigation; do not regenerate expectations to
hide a regression.

Failed comparisons leave diagnostic JSON in `/tmp/travel-mode-golden-actual`.
After reviewing an intentional behavior change, use:

```bash
uv run python tests/characterization/update_golden.py /path/to/reviewed-artifacts --acknowledge-behavior-change
```

Review the complete diff, including command order, recovery receipts, output and
exit codes. This command only updates known, complete contracts. Historical
checksums are not a substitute for reviewing current behavior.
