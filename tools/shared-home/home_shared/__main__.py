"""Offline commands; intentionally no production-enable or host-maintenance flag."""
import argparse
import json
from pathlib import Path

from .operations import preservation_gaps, source_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    manifest = commands.add_parser("source-manifest", help="hash explicit local source paths")
    manifest.add_argument("--root", type=Path, required=True)
    manifest.add_argument("paths", nargs="+")
    readiness = commands.add_parser("preservation-check", help="report missing baseline evidence; never deploy")
    readiness.add_argument("receipt", type=Path)
    args = parser.parse_args()
    if args.command == "source-manifest":
        print(json.dumps(source_manifest(args.root, args.paths), indent=2))
        return 0
    gaps = preservation_gaps(json.loads(args.receipt.read_text(encoding="utf8")))
    print(json.dumps({"gaps": gaps, "deployment_authorized": False}, indent=2))
    return 1 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
