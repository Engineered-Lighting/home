#!/usr/bin/env python3
"""qwen38-toolspec-audit.py — audit an EOC functions spec for the hazards
the Qwen3.8 migration cares about (docs/QWEN38-MIGRATION.md, Phase 0 +
research finding R6).

Two independent consumers constrain these specs, and they fail differently:

  * the TOOL PARSER (`--tool-call-parser qwen3_coder`), where a
    zero-argument tool under strict tool choice drives a documented doom
    loop (vllm#50989): the model emits an empty call, the parser yields no
    arguments, the turn never terminates; and
  * XGRAMMAR, which compiles `response_format: json_schema` and structured
    tool arguments. It rejects a documented set of JSON-Schema features
    outright — a spec using them fails at request time, not at startup.

Run this against the LIVE subentry dump (the storage is truth; the repo
copy of the component is containment-refactored and must never be
deployed). The audit is read-only and offline.

Usage:
  tools/qwen38-toolspec-audit.py FUNCTIONS.yaml
  tools/qwen38-toolspec-audit.py FUNCTIONS.yaml --json
  tools/qwen38-toolspec-audit.py FUNCTIONS.yaml --fail-on-hazard   # CI mode

Exit status: 0 clean (or hazards found without --fail-on-hazard),
             1 hazards found with --fail-on-hazard,
             2 the spec could not be parsed.
"""
from __future__ import annotations

import argparse
import json
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.exit("pyyaml required: pip install pyyaml")

# JSON-Schema features xgrammar does not compile. Hitting one of these turns
# a structured request into a runtime 400, so they are gate-blocking rather
# than advisory.
XGRAMMAR_UNSUPPORTED = {
    "$ref": "cross-references are not resolved by xgrammar",
    "$dynamicRef": "dynamic references unsupported",
    "allOf": "schema composition unsupported",
    "oneOf": "exclusive composition unsupported (anyOf is the supported form)",
    "not": "negation unsupported",
    "if": "conditional subschemas unsupported",
    "then": "conditional subschemas unsupported",
    "else": "conditional subschemas unsupported",
    "patternProperties": "pattern-keyed properties unsupported",
    "dependentSchemas": "dependent subschemas unsupported",
    "unevaluatedProperties": "unevaluated-* keywords unsupported",
    "unevaluatedItems": "unevaluated-* keywords unsupported",
    "contains": "array `contains` unsupported",
    "propertyNames": "propertyNames unsupported",
}

# Advisory: accepted, but historically lossy through the qwen3_coder parser
# or silently ignored by xgrammar. Worth knowing before a gate blames the
# model for a spec problem.
ADVISORY_KEYS = {
    "format": "`format` is not enforced by xgrammar; validate downstream",
    "additionalProperties": "honoured inconsistently; prefer explicit properties",
    "default": "defaults are not injected by the grammar",
}


def walk(node, path="", hits=None):
    """Yield (path, key, value) for every mapping key in a schema tree."""
    if hits is None:
        hits = []
    if isinstance(node, dict):
        for k, v in node.items():
            hits.append((path, k, v))
            walk(v, f"{path}.{k}" if path else k, hits)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, f"{path}[{i}]", hits)
    return hits


def audit_spec(spec: dict) -> dict:
    """Audit one tool spec. Returns a findings record."""
    name = spec.get("name", "<unnamed>")
    params = spec.get("parameters") or {}
    props = params.get("properties")
    required = params.get("required") or []

    findings = []

    # R6 / vllm#50989 — the zero-argument doom loop.
    if not props:
        findings.append({
            "severity": "hazard",
            "code": "zero-arg-tool",
            "detail": (
                "no `properties` — a zero-argument tool. Under "
                "`--tool-call-parser qwen3_coder` with strict tool choice this "
                "reproduces vllm#50989 (empty call, no arguments parsed, turn "
                "never terminates). Give it a nullable no-op argument or "
                "confirm the parser is not run in strict mode."
            ),
        })
    elif not required:
        findings.append({
            "severity": "advisory",
            "code": "no-required",
            "detail": ("properties present but `required` is empty — the model "
                       "may emit `{}`, which behaves like a zero-arg call"),
        })

    for path, key, value in walk(params):
        if key in XGRAMMAR_UNSUPPORTED:
            findings.append({
                "severity": "hazard",
                "code": f"xgrammar-{key}",
                "detail": f"`{key}` at `{path or '<root>'}` — {XGRAMMAR_UNSUPPORTED[key]}",
            })
        elif key in ADVISORY_KEYS:
            findings.append({
                "severity": "advisory",
                "code": f"advisory-{key}",
                "detail": f"`{key}` at `{path or '<root>'}` — {ADVISORY_KEYS[key]}",
            })

    return {
        "name": name,
        "n_properties": len(props or {}),
        "n_required": len(required),
        "findings": findings,
    }


def load_specs(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, list):
        raise ValueError("expected a top-level list of {spec:, function:} entries")
    specs = []
    for entry in doc:
        if isinstance(entry, dict) and "spec" in entry:
            specs.append(entry["spec"])
        elif isinstance(entry, dict) and "name" in entry:
            specs.append(entry)  # bare-spec form
    return specs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("functions", help="EOC functions YAML (live subentry dump)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--fail-on-hazard", action="store_true",
                    help="exit 1 if any hazard-severity finding is present")
    args = ap.parse_args()

    try:
        specs = load_specs(args.functions)
    except Exception as e:  # noqa: BLE001 - surface any parse problem plainly
        print(f"FATAL: cannot parse {args.functions}: {e}", file=sys.stderr)
        return 2

    results = [audit_spec(s) for s in specs]
    hazards = [(r["name"], f) for r in results for f in r["findings"]
               if f["severity"] == "hazard"]
    advisories = [(r["name"], f) for r in results for f in r["findings"]
                  if f["severity"] == "advisory"]

    if args.json:
        print(json.dumps({
            "n_tools": len(results),
            "n_hazards": len(hazards),
            "n_advisories": len(advisories),
            "tools": results,
        }, indent=2))
    else:
        print(f"=== EOC tool-spec audit: {args.functions} ===")
        print(f"tools audited: {len(results)}")
        print(f"hazards      : {len(hazards)}")
        print(f"advisories   : {len(advisories)}")
        if hazards:
            print("\n--- HAZARDS (gate-blocking) ---")
            for name, f in hazards:
                print(f"  [{f['code']}] {name}\n      {f['detail']}")
        if advisories:
            print("\n--- advisories ---")
            for name, f in advisories:
                print(f"  [{f['code']}] {name}: {f['detail']}")
        if not hazards:
            print("\nNo gate-blocking hazards. `qwen3_coder` + xgrammar can "
                  "compile this spec set as written.")
        print("\nTool inventory:")
        for r in sorted(results, key=lambda r: r["name"]):
            flag = " <-- ZERO-ARG" if r["n_properties"] == 0 else ""
            print(f"  {r['name']:<28} props={r['n_properties']:<3} "
                  f"required={r['n_required']}{flag}")

    return 1 if (hazards and args.fail_on_hazard) else 0


if __name__ == "__main__":
    raise SystemExit(main())
