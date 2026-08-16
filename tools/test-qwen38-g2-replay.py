#!/usr/bin/env python3
"""Tests for tools/gate-g2-labeler-replay.py.

Production validates the labeler's pass-2 reply with pydantic, which is not
installed on the AI box, so the gate reimplements it. A reimplementation
that drifts from the live contract measures the wrong thing and reports a
confident number, so these tests hold it to the live source:

  * enums are IMPORTED from the archived live ontology, never copied;
  * the required-field set is checked against the live `Pass2Result`
    definition, parsed out of `vlm/schema.py`, so adding a field there
    fails a test here;
  * the tolerant JSON extraction accepts the same shapes production does
    (fenced, inline, prose-wrapped) — a stricter parser would reject
    replies production accepts and understate candidate validity.

Run: python3 tools/test-qwen38-g2-replay.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "g2_replay", HERE / "gate-g2-labeler-replay.py")
G2 = importlib.util.module_from_spec(spec)
sys.modules["g2_replay"] = G2
spec.loader.exec_module(G2)

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


ARCHIVE = G2.LIVE_LABELER
HAVE_ARCHIVE = (ARCHIVE / "videolabeler" / "ontology.py").exists()

print("live_contract_parity")
if not HAVE_ARCHIVE:
    print(f"  SKIP  Phase-0 labeler archive not present at {ARCHIVE}")
    print("        (run tools/qwen38-phase0-archive.sh to enable these)")
    ENUMS = {"activity": ("cooking", "other"), "posture": ("standing",),
             "quality_flags": ("clear",)}
else:
    ENUMS = G2.live_enums()
    check("activity enum loaded from live ontology",
          "other" in ENUMS["activity"] and len(ENUMS["activity"]) > 5,
          str(ENUMS["activity"]))
    check("posture enum loaded from live ontology",
          "no_person" in ENUMS["posture"] and len(ENUMS["posture"]) > 5)
    check("quality flags loaded from live ontology",
          "clear" in ENUMS["quality_flags"])

    # If the live Pass2Result grows a required field, this gate would keep
    # passing replies that production rejects. Catch that here.
    src = (ARCHIVE / "videolabeler" / "vlm" / "schema.py").read_text(
        encoding="utf-8", errors="replace")
    body = src.split("class Pass2Result(BaseModel):", 1)[-1].split("\n\n", 1)[0]
    live_fields = set(re.findall(r"^\s{4}(\w+)\s*:", body, re.M))
    known = {"activity_primary", "activity_other_hint", "posture",
             "quality_flags", "uncertainty", "evidence", "evidence_timestamps"}
    check("live Pass2Result field set is unchanged",
          live_fields == known,
          f"live={sorted(live_fields)} known={sorted(known)}")


def valid(d):
    try:
        G2.validate_pass2(d, ENUMS)
        return True
    except ValueError:
        return False


GOOD = {
    "activity_primary": ENUMS["activity"][0],
    "activity_other_hint": None,
    "posture": ENUMS["posture"][0],
    "quality_flags": [ENUMS["quality_flags"][0]],
    "uncertainty": {"activity": 0.2, "posture": 0.1},
    "evidence": "a person stands at the counter",
    "evidence_timestamps": [1.0, 2.5],
}

print("\nvalidation_accepts_production_shapes")
check("a well-formed reply validates", valid(GOOD))
check("quality_flags may be empty", valid({**GOOD, "quality_flags": []}))
check("evidence_timestamps may be empty", valid({**GOOD, "evidence_timestamps": []}))
check("activity_other_hint may be a string",
      valid({**GOOD, "activity_other_hint": "folding laundry"}))
check("integer uncertainty is accepted",
      valid({**GOOD, "uncertainty": {"activity": 0, "posture": 1}}))

print("\nvalidation_rejects_what_production_rejects")
check("unknown activity rejected", not valid({**GOOD, "activity_primary": "juggling"}))
check("unknown posture rejected", not valid({**GOOD, "posture": "levitating"}))
check("unknown quality flag rejected", not valid({**GOOD, "quality_flags": ["sparkly"]}))
check("missing activity rejected",
      not valid({k: v for k, v in GOOD.items() if k != "activity_primary"}))
check("missing uncertainty rejected",
      not valid({k: v for k, v in GOOD.items() if k != "uncertainty"}))
check("missing evidence rejected",
      not valid({k: v for k, v in GOOD.items() if k != "evidence"}))
check("uncertainty out of range rejected",
      not valid({**GOOD, "uncertainty": {"activity": 1.5, "posture": 0.1}}))
check("negative uncertainty rejected",
      not valid({**GOOD, "uncertainty": {"activity": -0.1, "posture": 0.1}}))
check("uncertainty missing a key rejected",
      not valid({**GOOD, "uncertainty": {"activity": 0.2}}))
check("non-numeric uncertainty rejected",
      not valid({**GOOD, "uncertainty": {"activity": "low", "posture": 0.1}}))
# bool is an int subclass in Python — a naive isinstance check passes it.
check("boolean uncertainty rejected",
      not valid({**GOOD, "uncertainty": {"activity": True, "posture": 0.1}}))
check("quality_flags as a string rejected", not valid({**GOOD, "quality_flags": "clear"}))
check("evidence_timestamps of strings rejected",
      not valid({**GOOD, "evidence_timestamps": ["1.0"]}))
check("non-dict top level rejected", not valid(["not", "an", "object"]))

print("\njson_extraction_matches_production_tolerance")
ex = G2.extract_json
check("bare JSON object", ex('{"a": 1}') == {"a": 1})
check("fenced json block", ex('```json\n{"a": 2}\n```') == {"a": 2})
check("fenced block without a language tag", ex('```\n{"a": 3}\n```') == {"a": 3})
check("JSON embedded in prose",
      ex('Here is the label: {"a": 4} — hope that helps') == {"a": 4})
check("nested braces survive the balanced scan",
      ex('noise {"a": {"b": [1,2]}} tail') == {"a": {"b": [1, 2]}})
check("first object wins when several appear",
      ex('{"a": 1} then {"b": 2}') == {"a": 1})
for bad, label in ((None, "None"), ("", "empty string"), ("   ", "whitespace"),
                   ("no json here", "prose with no object"),
                   ("[1,2,3]", "a JSON array, not an object")):
    try:
        ex(bad)
        check(f"{label} raises", False, "no exception")
    except ValueError:
        check(f"{label} raises", True)

print("\nend_to_end_scoring")
sys.path.insert(0, str(HERE))
import qwen38_gates as gates  # noqa: E402

# Candidate strictly better on validity -> PASS.
pairs = [(False, True)] * 14 + [(True, False)] * 1 + [(True, True)] * 500
r = gates.mcnemar_from_pairs("G2", pairs)
check("candidate improvement passes", r.passed)
check("concordant windows are ties", r.ties == 500)
# Candidate worse -> FAIL.
r = gates.mcnemar_from_pairs("G2", [(True, False)] * 14 + [(False, True)] * 1)
check("candidate regression fails", not r.passed)

print()
if FAILURES:
    print(f"qwen38 G2 replay tests: {len(FAILURES)} FAILED -> {FAILURES}")
    sys.exit(1)
print("qwen38 G2 replay tests: all passed")
