#!/bin/bash
# SwiftBar/xbar plugin. Set TRAVEL_MODE_BIN when the executable is not on PATH.
set -euo pipefail
TM_BIN="${TRAVEL_MODE_BIN:-travel-mode}"
json="$($TM_BIN check --json)"
if printf '%s' "$json" | grep -q '"exists": true'; then icon='✈️'; else icon='🔋'; fi
printf '%s | refresh=true\n' "$icon"
printf -- '---\n'
for profile in flight overnight cafe; do
  printf 'On (%s) | bash="%s" param1="on" param2="--profile" param3="%s" param4="--yes" terminal=true refresh=true\n' "$profile" "$TM_BIN" "$profile"
done
printf 'Off | bash="%s" param1="off" terminal=true refresh=true\n' "$TM_BIN"
printf 'Check | bash="%s" param1="check" terminal=false refresh=true\n' "$TM_BIN"
printf 'Open config | bash="open" param1="%s" terminal=false\n' "$(dirname "${TRAVEL_MODE_CONFIG:-$HOME/.config/travel-mode/config.toml}")"
