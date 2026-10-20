"""Vendor the canonical observation consumer without importing workstation code.

Run after changing adapter.py, or use --check in workstation validation. This
only writes the repository's inactive custom-component source, never HA config.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools/shared-home/home_shared/adapter.py"
OUTPUT = ROOT / "ha-config/custom_components/shared_home_context/consumer.py"
NAMES = ("finite", "valid_observation", "CurrentContext")


def render_consumer(source: str | None = None) -> str:
    source = SOURCE.read_text(encoding="utf8") if source is None else source
    tree = ast.parse(source)
    nodes = {node.name: node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in NAMES}
    if set(nodes) != set(NAMES):
        raise ValueError("canonical observation consumer declarations changed")
    body = "\n\n\n".join(ast.get_source_segment(source, nodes[name]) for name in NAMES) + "\n"
    digest = hashlib.sha256(body.encode()).hexdigest()
    return ('"""Generated observation consumer; do not edit directly.\n\n'
            'Source: tools/shared-home/home_shared/adapter.py\n'
            'Regenerate: python tools/shared-home/build_ha_consumer.py\n'
            f'Extracted source SHA256: {digest}\n'
            '"""\nfrom __future__ import annotations\n\nimport math\nfrom typing import Any\n\n\n' + body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    rendered = render_consumer()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf8") != rendered:
            print("HA consumer is stale; run python tools/shared-home/build_ha_consumer.py")
            return 1
        print("HA consumer matches the canonical observation contract")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf8", newline="\n")
    print("Generated inactive HA observation consumer source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
