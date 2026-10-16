#!/bin/bash
# SwiftBar runs this finite reader every five minutes; actions open a terminal.
set -euo pipefail
export TRAVEL_MODE_BIN="${TRAVEL_MODE_BIN:-travel-mode}"
json="$("$TRAVEL_MODE_BIN" status --json)"
# Native JSON parsing; no Python environment, scan or secondary product command.
/usr/bin/plutil -extract swiftbar raw -o - - <<< "$json"
