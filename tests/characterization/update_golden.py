"""Update reviewed golden contracts after an acknowledged behavior change."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--acknowledge-behavior-change", action="store_true", required=True)
    args = parser.parse_args()
    if not args.artifact_dir.is_dir():
        parser.error(f"Artifact directory does not exist: {args.artifact_dir}")
    sources = sorted(args.artifact_dir.glob("*.json"))
    if not sources:
        parser.error("Artifact directory contains no JSON contracts")
    target_dir = Path(__file__).with_name("golden")
    updates = []
    for source in sources:
        target = target_dir / source.name
        if not target.is_file():
            parser.error(f"Unknown golden: {source.name}")
        payload = source.read_text()
        data = json.loads(payload)
        required = {"commands", "state", "config", "output", "exit_code"}
        if not isinstance(data, dict) or not required.issubset(data):
            parser.error(f"Incomplete contract: {source}")
        updates.append((target, payload))
    for target, payload in updates:
        target.write_text(payload)
        print(target)


if __name__ == "__main__":
    main()
