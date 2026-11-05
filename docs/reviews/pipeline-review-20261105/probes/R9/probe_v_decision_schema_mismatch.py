#!/usr/bin/env python3
"""Probe: decide's Decision records (rung 9) against schemas.json's Decision fields (canon), and what a consumer
written from schemas.json reads out of them.

    python3 -B probe_v_decision_schema_mismatch.py <den-core-live checkout root>

It takes every decide decision from the hold fixtures V62 and V64 under P1 (holds.run_case, events handed to decide
alone) plus one synthetic evening run through the runner (which adds `actuation`), and checks:
  - enum values: trigger, status, applied_scene[].status against the schema's lists;
  - per-channel field names: requested_scene {level, cct_k, target_cct_k, clip_reason, aim}, applied_scene[].readback
    {level, cct_k, aim}, inputs.actuator_state {level, cct_k, ...}, inputs.hold_set.held[].pinned {level_pct, cct_k,
    source}, candidates [{scene, cost, why}], and the presence of `targets`;
  - inputs.light_model as a version string (schema: "light model versions");
  - a consumer reading the canon names the way tools/den-verify does on claude/rung-10-verify (score.py state_at:
    float(readback.get('level') or 0); pinned.get('level_pct')): the level it gets for each lit channel.
The enums and field names are read from docs/architecture/live-v1/schemas.json in the checkout.
Synthetic data only; nothing written; no host contacted. Prints one RESULT line per check and FAIL when any differ.
"""
import json
import os
import re
import sys


def main():
    root = os.path.abspath(sys.argv[1])
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.join(root, "tools", "den-core"))
    import holds
    import runner
    import synthetic

    schema = json.load(open(os.path.join(root, "docs", "architecture", "live-v1", "schemas.json"), encoding="utf-8"))
    dec = next(r for r in schema["records"] if r["id"] == "decision")
    fields = {f["name"]: f for f in dec["fields"]}
    trig = [s.strip() for s in fields["trigger"]["meaning"].split("(")[0].split("|")]
    status = [s.strip() for s in fields["status"]["type"].split("|")]
    applied_status = re.search(r"`status` ([^.]*)\.", fields["applied_scene"]["meaning"]).group(1)
    applied_status = [s.strip() for s in applied_status.split("|")]
    print("RESULT schema enums: trigger=%s status=%s applied.status=%s" % (trig, status, applied_status))

    decisions = []
    for case in ("V62", "V64"):
        decisions += [e["data"] for e in holds.run_case(case, "P1")[1]]
    items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True, game=True)
    _, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need", "incumbent", "decide"]),
                        synthetic=True, sources=[{"name": "synthetic:evening"}])
    decisions += [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]
    print("RESULT %d decide decisions checked" % len(decisions))

    problems = {}

    def note(key, example):
        problems.setdefault(key, example)

    lit_levels = []
    for d in decisions:
        if d["trigger"] not in trig:
            note("trigger %r not in the schema's enum" % d["trigger"], d["decision_id"])
        if d["status"] not in status:
            note("status %r not in the schema's enum" % d["status"], d["decision_id"])
        if "targets" not in d:
            note("no `targets` field", d["decision_id"])
        if not isinstance(d["inputs"].get("light_model"), str):
            note("inputs.light_model is a %s, not a version string" % type(d["inputs"]["light_model"]).__name__,
                 d["decision_id"])
        for c in d["candidates"]:
            if "scene" not in c:
                note("candidates[] has %s, not {scene, cost, why}" % sorted(c), d["decision_id"])
        for ch, s in d["requested_scene"].items():
            missing = {"level", "cct_k", "target_cct_k", "clip_reason"} - set(s or {})
            if missing:
                note("requested_scene[] keys %s lack %s" % (sorted(s), sorted(missing)), d["decision_id"])
        for ch, a in d["applied_scene"].items():
            if a["status"] not in applied_status:
                note("applied_scene[].status %r not in the schema's enum" % a["status"], d["decision_id"])
            rb = a.get("readback") or {}
            if "level" not in rb:
                note("applied_scene[].readback keys %s lack level/cct_k" % sorted(rb), d["decision_id"])
            if rb.get("on"):
                lit_levels.append(float(rb.get("level") or 0))  # den-verify score.py state_at, verbatim read
        for ch, s in (d["inputs"].get("actuator_state") or {}).items():
            if s is not None and "level" not in s:
                note("inputs.actuator_state[] keys %s lack level/cct_k/as_of/source" % sorted(s), d["decision_id"])
        for h in d["inputs"]["hold_set"]["held"]:
            p = h.get("pinned") or {}
            if "level_pct" not in p or p.get("source") != "read-back":
                note("hold_set.held[].pinned keys %s lack level_pct/cct_k/source" % sorted(p), d["decision_id"])
            if "channels" not in h or "cause" not in h:
                note("hold_set.held[] keys %s lack channels/cause" % sorted(h), d["decision_id"])
    for key, example in sorted(problems.items()):
        print("FAIL %s (e.g. %s)" % (key, example))
    if lit_levels:
        print("RESULT a schema-canon consumer (den-verify's float(readback.get('level') or 0)) reads %d lit applied "
              "channel(s) as level %s" % (len(lit_levels), sorted(set(lit_levels))))
    print("RESULT %s" % ("PASS: matches schemas.json" if not problems else "%d mismatch kinds" % len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
