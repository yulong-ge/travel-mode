"""Update reviewed golden contracts after an acknowledged behavior change."""

import argparse
import json
from pathlib import Path

from contracts import canonical_name, load_contract


def update(artifact_dir, target_dir):
    if not artifact_dir.is_dir():
        raise ValueError(f"Artifact directory does not exist: {artifact_dir}")
    sources = sorted(artifact_dir.glob("*.json"))
    if not sources:
        raise ValueError(f"Artifact directory contains no JSON contracts: {artifact_dir}")
    updates = {}
    for source in sources:
        target = target_dir / f"{canonical_name(source.stem)}.json"
        if not target.is_file():
            raise ValueError(f"Unknown golden: {source.name}")
        data = load_contract(source)
        if target in updates and updates[target] != data:
            raise ValueError(
                f"Conflicting aliases for {target.name}: {source}; split the alias before updating"
            )
        updates[target] = data
    # Validate every input and alias before changing any expected file.
    for target, data in updates.items():
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        target.write_text(payload)
        print(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--acknowledge-behavior-change", action="store_true", required=True)
    args = parser.parse_args()
    update(args.artifact_dir, Path(__file__).with_name("golden"))


if __name__ == "__main__":
    main()
