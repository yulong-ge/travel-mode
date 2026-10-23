#!/bin/bash
# travel-mode SwiftBar plugin: io.github.yulong-ge.travel-mode
# <xbar.title>travel-mode</xbar.title>
# <xbar.version>2.2.0</xbar.version>
# <xbar.author>Long-louis</xbar.author>
# <xbar.author.github>yulong-ge</xbar.author.github>
# <xbar.desc>Local session and power-source status; five-minute refresh.</xbar.desc>
# <xbar.abouturl>https://github.com/yulong-ge/travel-mode</xbar.abouturl>
# Metadata and parameters: https://github.com/swiftbar/SwiftBar#hiding-default-items
# https://github.com/swiftbar/SwiftBar#parameters
# Title symbols are embedded with symbolize=true and sfsize=16; no separate sfimage title.
# https://github.com/swiftbar/SwiftBar/blob/v2.1.1/README.md#parameters
# No refreshOnOpen, stream mode or resident process. One status query per .5m refresh.
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
set -uo pipefail

resolve_binary() {
    local candidate resolved
    for candidate in "${TRAVEL_MODE_BIN:-}" "$HOME/.local/bin/travel-mode" \
        /opt/homebrew/bin/travel-mode /usr/local/bin/travel-mode travel-mode; do
        [[ -n "$candidate" ]] || continue
        if [[ "$candidate" == */* ]]; then
            [[ "$candidate" == /* && -f "$candidate" && -x "$candidate" ]] || continue
            printf '%s\n' "$candidate"
            return 0
        fi
        resolved="$(command -v "$candidate" 2>/dev/null)" || continue
        [[ "$resolved" == /* && -f "$resolved" && -x "$resolved" ]] || continue
        printf '%s\n' "$resolved"
        return 0
    done
    return 1
}

warning() {
    printf '%s\n' ':exclamationmark.triangle: | symbolize=true sfsize=16 dropdown=false emojize=false' '---'
    # Bootstrap-only text: status normally supplies every localized menu line.
    if [[ "${TRAVEL_MODE_LANG:-${LANG:-en}}" == zh* ]]; then
        if [[ "$1" == missing ]]; then
            printf '%s\n' '未找到 travel-mode。请安装或设置 TRAVEL_MODE_BIN。'
            printf '%s\n' '安装 travel-mode | sfimage=arrow.down.circle bash=/bin/bash param1=-lc param2="uv tool install git+https://github.com/yulong-ge/travel-mode" terminal=true refresh=true'
        else
            printf '%s\n' '无法读取 travel-mode 状态。请在终端运行 travel-mode status --json 查看错误。'
        fi
    else
        if [[ "$1" == missing ]]; then
            printf '%s\n' 'travel-mode was not found. Install it or set TRAVEL_MODE_BIN.'
            printf '%s\n' 'Install travel-mode | sfimage=arrow.down.circle bash=/bin/bash param1=-lc param2="uv tool install git+https://github.com/yulong-ge/travel-mode" terminal=true refresh=true'
        else
            printf '%s\n' 'Could not read travel-mode status. Run travel-mode status --json in Terminal for the error.'
        fi
    fi
}

if ! TRAVEL_MODE_BIN="$(resolve_binary)"; then
    warning missing
    exit 0
fi
export TRAVEL_MODE_BIN
# A failed status may still supply a localized error menu. Never run a full check.
json="$("$TRAVEL_MODE_BIN" status --json)"
status_rc=$?
if menu="$(/usr/bin/plutil -extract swiftbar raw -o - - <<< "$json" 2>/dev/null)"; then
    if [[ -n "$menu" ]]; then
        printf '%s\n' "$menu"
        exit 0
    fi
    # Standard plugins hide with exit 0 and empty stdout, including no newline.
    # https://github.com/swiftbar/SwiftBar/blob/v2.1.1/README.md#standard-default
    if [[ "$status_rc" -eq 0 ]]; then
        exit 0
    fi
fi
warning failed
exit "$((status_rc == 0 ? 1 : status_rc))"
