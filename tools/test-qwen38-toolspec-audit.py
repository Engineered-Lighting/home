#!/usr/bin/env python3
"""Tests for tools/qwen38-toolspec-audit.py.

The auditor is a migration gate input: it decides whether the live EOC tool
catalogue is safe to run under `--tool-call-parser qwen3_coder` with
xgrammar-backed structured output. A false negative here means a doom loop
in production, so the zero-argument and xgrammar-feature detections are
tested against the exact shapes the research findings name.

Run: python3 tools/test-qwen38-toolspec-audit.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "toolspec_audit", HERE / "qwen38-toolspec-audit.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def codes(result: dict) -> set[str]:
    return {f["code"] for f in result["findings"]}


def severities(result: dict, severity: str) -> list[dict]:
    return [f for f in result["findings"] if f["severity"] == severity]


print("zero_argument_detection")
# vllm#50989: a tool with no properties at all.
r = audit.audit_spec({"name": "areas_in_home", "parameters": {"type": "object"}})
check("no properties key is a hazard", "zero-arg-tool" in codes(r))
check("zero-arg is hazard severity", len(severities(r, "hazard")) == 1)

r = audit.audit_spec({"name": "t", "parameters": {"type": "object", "properties": {}}})
check("empty properties dict is a hazard", "zero-arg-tool" in codes(r))

# A tool with args but nothing required can still be called with {} — the
# same wire shape as a zero-arg call, but recoverable, so: advisory.
r = audit.audit_spec({
    "name": "find_clips",
    "parameters": {"type": "object", "properties": {"camera": {"type": "string"}}},
})
check("properties without required is advisory, not hazard",
      "no-required" in codes(r) and not severities(r, "hazard"))

r = audit.audit_spec({
    "name": "get_room_state",
    "parameters": {"type": "object",
                   "properties": {"room": {"type": "string"}},
                   "required": ["room"]},
})
check("well-formed tool is clean", not r["findings"], str(r["findings"]))
check("property count is reported", r["n_properties"] == 1 and r["n_required"] == 1)

print("\nxgrammar_unsupported_features")
for key in ("$ref", "allOf", "oneOf", "not", "patternProperties", "if"):
    r = audit.audit_spec({
        "name": "t",
        "parameters": {"type": "object", "required": ["a"],
                       "properties": {"a": {key: "x"}}},
    })
    check(f"`{key}` is a hazard", f"xgrammar-{key}" in codes(r))

# anyOf IS supported by xgrammar — flagging it would be a false positive
# that sends someone rewriting a working schema.
r = audit.audit_spec({
    "name": "t",
    "parameters": {"type": "object", "required": ["a"],
                   "properties": {"a": {"anyOf": [{"type": "string"}]}}},
})
check("`anyOf` is NOT flagged (xgrammar supports it)",
      not any(c.startswith("xgrammar-") for c in codes(r)), str(codes(r)))

print("\nnested_detection")
# The hazard must be found however deep it is nested — real specs bury
# these inside array items.
r = audit.audit_spec({
    "name": "execute_services",
    "parameters": {
        "type": "object", "required": ["list"],
        "properties": {"list": {"type": "array", "items": {
            "type": "object",
            "properties": {"target": {"$ref": "#/defs/target"}}}}},
    },
})
check("nested $ref inside array items is found", "xgrammar-$ref" in codes(r))

print("\nadvisory_keys")
r = audit.audit_spec({
    "name": "t",
    "parameters": {"type": "object", "required": ["a"],
                   "properties": {"a": {"type": "string", "format": "date-time"}}},
})
check("`format` is advisory only",
      "advisory-format" in codes(r) and not severities(r, "hazard"))

print("\nloader")
with tempfile.TemporaryDirectory() as td:
    p = pathlib.Path(td) / "functions.yaml"
    p.write_text(
        "- spec:\n"
        "    name: zero_arg\n"
        "    parameters:\n"
        "      type: object\n"
        "  function:\n"
        "    type: native\n"
        "- spec:\n"
        "    name: good\n"
        "    parameters:\n"
        "      type: object\n"
        "      properties:\n"
        "        x: {type: string}\n"
        "      required: [x]\n"
        "  function:\n"
        "    type: native\n",
        encoding="utf-8")
    specs = audit.load_specs(str(p))
    check("loads the EOC {spec:, function:} envelope", len(specs) == 2)
    check("unwraps to the spec body",
          {s["name"] for s in specs} == {"zero_arg", "good"})
    results = [audit.audit_spec(s) for s in specs]
    hazards = [f for r in results for f in r["findings"] if f["severity"] == "hazard"]
    check("finds exactly the one zero-arg tool", len(hazards) == 1)

    bad = pathlib.Path(td) / "bad.yaml"
    bad.write_text("not: a list\n", encoding="utf-8")
    try:
        audit.load_specs(str(bad))
        check("non-list spec file raises", False, "no exception")
    except ValueError:
        check("non-list spec file raises", True)

print()
if FAILURES:
    print(f"qwen38 toolspec audit tests: {len(FAILURES)} FAILED -> {FAILURES}")
    sys.exit(1)
print("qwen38 toolspec audit tests: all passed")
