"""Public files must not expose private machine identifiers.

Patterns are encoded so the regression guard does not itself publish them.
PNG pixels also require visual review; a byte scan cannot inspect compressed pixels.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "travel_mode"
PRIVATE_PATTERNS = (
    "bWFjYm9va2Fpcg==",
    "L1VzZXJzL21hY2Jvb2thaXI=",
    "Y29tLm1hY2Jvb2thaXIu",
    "cmVzdGlj",
    "Z2RyaXZl",
    "Y2MtY29ubmVjdA==",
    "ZGV2c3BhY2U=",
    "Y2xvdWRmbGFyZWQuZGV2c3BhY2U=",
    "cmF5c3luYw==",
    "Y2l0cm9sYWJz",
    "RWdvVXBkYXRlcg==",
    "YXNyLXByb3h5",
    "Y29tLmZycC5tYWNib29r",
    "aGVyZHI=",
    "YXNpbW92",
    "aHlwZXJDb25uZWN0",
    "eGlhb21p",
    "WFpQcmludENsaWVudA==",
    "T3JiU3RhY2s=",
    "5ZCM6Iqx6aG6",
    "5rOi54K56Z+z5LmQ",
    "d3Bzb2ZmaWNl",
    "VGlja1RpY2s=",
    "UnN5bmNVSQ==",
    "TmV0TmV3c1dpcmU=",
    "T3BlbkNoYW1iZXI=",
    "UGFzZW8=",
    "Q2hlcnJ5",
    "RG91YmFv",
    "UWlhbndlbg==",
    "U3BhcmsgRGVza3RvcA==",
    "VHlwb3Jh",
    "U3VibGltZSBUZXh0",
    "MjMzOTY0NTE=",
    "MjM2NTg1OTU=",
    "L3Zhci9mb2xkZXJzL3Y5L3hyZGdkdHluMThqYzhkc2ZqcGdyMGdoaDAwMDBnbg==",
    "L3ByaXZhdGUvdmFyL2ZvbGRlcnMvdjkv",
    "bG9uZ+eahE1hY0Jvb2sgUHJv",
    "bG9uZ2RlTWFjQm9vay1Qcm8=",
    "5L2c6ICF5pys5py6",
)
SOURCE_PATTERNS = "ZnJwCnN5bmN0aGluZwpzYW5nZm9y"
BANNED_IN_LOCALES = (
    "Tailscale",
    "Clash",
    "mihomo",
    "Mihomo",
    "aTrust",
    "UURemote",
    "Surge",
    "Shadowrocket",
    "WireGuard",
)


def test_public_files_have_no_personal_identifiers():
    patterns = [base64.b64decode(value).decode().casefold() for value in PRIVATE_PATTERNS]
    offenders = []
    excluded = {".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__", "dist", "build", "htmlcov"}
    # Prune local runtimes/caches; scan every other extension even without Git metadata.
    for directory, children, files in os.walk(ROOT):
        children[:] = [name for name in children if name not in excluded and not name.endswith(".egg-info")]
        for name in files:
            if name == ".coverage":
                continue
            path = Path(directory) / name
            payload = path.read_bytes().lower()
            # Include binary/generator artifacts and UTF-16 text, not just Python sources.
            for number, pattern in enumerate(patterns):
                if any(pattern.encode(encoding) in payload for encoding in ("utf-8", "utf-16le", "utf-16be")):
                    offenders.append(f"{path.relative_to(ROOT)}: private pattern {number}")
    assert offenders == []


def test_source_has_no_personal_service_names():
    patterns = base64.b64decode(SOURCE_PATTERNS).decode().splitlines()
    offenders = []
    for path in SRC.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        if any(word in content for word in patterns):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_locales_name_no_specific_apps():
    offenders = []
    for path in (SRC / "locales").glob("*.toml"):
        content = path.read_text(encoding="utf-8")
        for word in BANNED_IN_LOCALES:
            if word in content:
                offenders.append(f"{path.name}: {word}")
    assert offenders == []
