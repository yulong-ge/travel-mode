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
    "Y2xvdWRmbGFyZWQuZGV2c3BhY2U=",
    "Y2l0cm9sYWJz",
    "RWdvVXBkYXRlcg==",
    "YXNyLXByb3h5",
    "Y29tLmZycC5tYWNib29r",
    "aGVyZHI=",
    "YXNpbW92",
    "aHlwZXJDb25uZWN0",
    "MjMzOTY0NTE=",
    "MjM2NTg1OTU=",
    "L3Zhci9mb2xkZXJzL3Y5L3hyZGdkdHluMThqYzhkc2ZqcGdyMGdoaDAwMDBnbg==",
    "L3ByaXZhdGUvdmFyL2ZvbGRlcnMvdjkv",
    "bG9uZ+eahE1hY0Jvb2sgUHJv",
    "bG9uZ2RlTWFjQm9vay1Qcm8=",
    "5L2c6ICF5pys5py6",
    "Y29tLnVzZXIuY2MtY29ubmVjdA==",
    "b3JnLnVzZXIuZGV2c3BhY2U=",
    "Y29tLnVzZXIucmVzdGljLmJhY2t1cA==",
    "aG9tZWJyZXcubXhjbC5mcnBjLXBlcnNvbmFs",
)

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
            if name == ".git" or name == ".coverage":
                continue
            path = Path(directory) / name
            payload = path.read_bytes().lower()
            # Include binary/generator artifacts and UTF-16 text, not just Python sources.
            for number, pattern in enumerate(patterns):
                if any(pattern.encode(encoding) in payload for encoding in ("utf-8", "utf-16le", "utf-16be")):
                    offenders.append(f"{path.relative_to(ROOT)}: private pattern {number}")
    assert offenders == []


def test_source_has_no_personal_service_names():
    patterns = [base64.b64decode(value).decode().casefold() for value in PRIVATE_PATTERNS]
    offenders = []
    for path in SRC.rglob("*.py"):
        content = path.read_text(encoding="utf-8").casefold()
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


def test_worktree_git_file_is_excluded_without_reading_it(tmp_path, monkeypatch):
    (tmp_path / ".git").write_text("gitdir: /fixture/private-worktree")
    (tmp_path / "public.txt").write_text("Public fixture")
    monkeypatch.setattr(__import__(__name__, fromlist=["ROOT"]), "ROOT", tmp_path)
    read = Path.read_bytes

    def checked_read(path):
        assert path.name != ".git", "Git metadata must not be read by the public-file scanner"
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", checked_read)
    test_public_files_have_no_personal_identifiers()


def test_privacy_scan_still_checks_other_hidden_files(tmp_path, monkeypatch):
    import pytest

    (tmp_path / ".private-note").write_bytes(base64.b64decode(PRIVATE_PATTERNS[0]))
    monkeypatch.setattr(__import__(__name__, fromlist=["ROOT"]), "ROOT", tmp_path)
    with pytest.raises(AssertionError, match="private-note"):
        test_public_files_have_no_personal_identifiers()


def test_public_products_allowed_but_private_service_labels_rejected(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(__import__(__name__, fromlist=["ROOT"]), "ROOT", tmp_path)
    public = tmp_path / "products.txt"
    public.write_text(
        "restic OrbStack WPS TickTick NetNewsWire Syncthing Tailscale frp Keystone "
        "gdrive cc-connect DevSpace Raysync"
    )
    test_public_files_have_no_personal_identifiers()
    public.write_bytes(base64.b64decode(PRIVATE_PATTERNS[-1]))
    with pytest.raises(AssertionError, match=r"products\.txt"):
        test_public_files_have_no_personal_identifiers()
