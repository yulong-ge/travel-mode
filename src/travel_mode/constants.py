"""Portable built-in defaults.

Personal app and service choices belong in the user's TOML configuration.
"""

POWER_KEYS = [
    ("lowpowermode", "Low Power Mode", "1"),
    ("sleep", "Idle minutes before system sleep", "5"),
    ("displaysleep", "Minutes before display sleep", "3"),
    ("powernap", "Power Nap", "0"),
    ("tcpkeepalive", "Keep TCP connections during sleep", "1"),
    ("womp", "Wake on network access", "0"),
    ("standby", "Deep standby", "1"),
    ("hibernatemode", "Hibernation mode", "3"),
    ("ttyskeepawake", "Prevent sleep while a terminal is active", "1"),
    ("disksleep", "Minutes before disk sleep", "10"),
    ("lessbright", "Dim display on battery", "1"),
    ("networkoversleep", "Network during sleep", "0"),
]
POWER_KEY_ORDER = [key for key, _, _ in POWER_KEYS]

DEFAULT_APPS = [
    "Google Chrome",
    "Microsoft Edge",
    "Firefox",
    "Brave Browser",
    "Arc",
    "Slack",
    "Discord",
    "Microsoft Teams",
    "zoom.us",
    "Spotify",
    "Docker",
    "Visual Studio Code",
    "Cursor",
    "Notion",
    "Obsidian",
    "Figma",
    "Telegram",
    "WhatsApp",
    "WeChat",
    "QQ",
    "DingTalk",
    "Lark",
    "ChatGPT",
    "Claude",
    "Steam",
    "OneDrive",
    "Dropbox",
]
DEFAULT_ACTIONS = {
    "freeze": [
        "WeChat",
        "QQ",
        "Lark",
        "DingTalk",
        "Slack",
        "Discord",
        "Telegram",
        "WhatsApp",
        "Notion",
        "Obsidian",
        "ChatGPT",
        "Claude",
    ],
    "quit": [
        "Spotify",
        "Docker",
        "Steam",
        "zoom.us",
        "Microsoft Teams",
        "Figma",
        "OneDrive",
        "Dropbox",
    ],
    "ask": [
        "Google Chrome",
        "Microsoft Edge",
        "Firefox",
        "Brave Browser",
        "Arc",
        "Visual Studio Code",
        "Cursor",
    ],
    "skip": [],
}
DEFAULT_NET_APPS = [
    "aTrust",
    "Sangfor",
    "EasyConnect",
    "Clash Party",
    "ClashX Meta",
    "Mihomo Party",
    "V2rayU",
    "Cloudflare WARP",
    "UURemote",
    "Tailscale",
    "ClashX",
    "ClashX Pro",
    "Clash Verge",
    "Surge",
    "Shadowrocket",
    "WireGuard",
]
DEFAULT_SERVICES_UNLOAD = []
DEFAULT_SERVICES_KEEP = []
DEFAULT_DAEMON_APPS = []

# Compatibility aliases for existing configuration consumers.
APPS = DEFAULT_APPS
UNLOAD_SVCS = []
KEEP_SVCS = []
LIST_KEYS = ("quit_apps", "frozen_apps", "unloaded_svcs", "unloaded_sys_svcs")
STR_KEYS = (
    "batt_prof",
    "ac_prof",
    "sleepdisabled",
    "spotlight_off",
    "wifi_off",
    "bt_off",
    "brightness",
    "anchored_at",
)

__all__ = [
    "APPS",
    "DEFAULT_ACTIONS",
    "DEFAULT_APPS",
    "DEFAULT_DAEMON_APPS",
    "DEFAULT_NET_APPS",
    "DEFAULT_SERVICES_KEEP",
    "DEFAULT_SERVICES_UNLOAD",
    "KEEP_SVCS",
    "LIST_KEYS",
    "POWER_KEYS",
    "POWER_KEY_ORDER",
    "STR_KEYS",
    "UNLOAD_SVCS",
]
