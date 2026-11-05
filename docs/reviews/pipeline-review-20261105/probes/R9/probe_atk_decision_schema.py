"""probe_atk_decision_schema: decide's Decision records against schemas.json's Decision (field names are canon) and
against what rung 10 (tools/den-verify on claude/rung-10-verify) reads from a Decision.

What it checks, on the decide decisions of a synthetic evening (need, incumbent, decide; touch and game):
  - requested_scene per channel: canon {level, cct_k, target_cct_k, clip_reason, aim}
  - applied_scene[].readback: canon {level, cct_k, aim}; applied_scene[].status: canon enum
    sent|held|superseded|stopped|repaired|completed_after_check|failed
  - inputs.hold_set.held[].pinned: canon {level_pct, cct_k, aim, source} (source: read-back)
  - inputs.actuator_state per channel: canon {on, level, v_hat, cct_k, aim, motion_status, source, as_of}
  - Decision.status: canon applied|partial|superseded|kept_current
  - inputs.light_model: rung 10's score.Dec compares it with its loaded model's version STRING
  The canon field lists are read from the live-records contracts if a path is given, else from the copies in the
  checkout's docs/architecture/live-v1/schemas.json.

How to run (synthetic data only; no files written; no host contacted):
  python3 -B probe_atk_decision_schema.py /path/to/den-core-live-checkout
"""
import json
import sys
from pathlib import Path

CANON = {
    "requested_scene": {"level", "cct_k", "target_cct_k", "clip_reason", "aim"},
    "readback": {"level", "cct_k", "aim"},
    "pinned": {"level_pct", "cct_k", "aim", "source"},
    "actuator_state": {"on", "level", "v_hat", "cct_k", "aim", "motion_status", "source", "as_of"},
}
APPLIED_STATUS = {"sent", "held", "superseded", "stopped", "repaired", "completed_after_check", "failed"}
DECISION_STATUS = {"applied", "partial", "superseded", "kept_current"}


def main():
    root = Path(sys.argv[1]).resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / "tools" / "den-core"))
    schemas = json.loads((root / "docs" / "architecture" / "live-v1" / "schemas.json").read_text("utf-8"))
    dec = next(r for r in schemas["records"] if r["name"] == "Decision")
    types_ = {f["name"]: f["type"] for f in dec["fields"]}
    print("INFO canon requested_scene type: %s" % types_["requested_scene"])
    print("INFO canon applied_scene type: %s" % types_["applied_scene"])
    print("INFO canon inputs.hold_set type: %s" % types_["inputs.hold_set"])
    print("INFO canon status type: %s" % types_["status"])
    import runner
    import synthetic
    items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True, game=True)
    header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need", "incumbent", "decide"]),
                             synthetic=True)
    decided = [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]
    seen = {"requested_scene": set(), "readback": set(), "pinned": set(), "actuator_state": set()}
    applied_status, status, lm_types = set(), set(), set()
    for d in decided:
        for s in d["requested_scene"].values():
            seen["requested_scene"] |= set(s)
        for a in d["applied_scene"].values():
            applied_status.add(a["status"])
            if a.get("readback"):
                seen["readback"] |= set(a["readback"])
        for h in d["inputs"]["hold_set"]["held"]:
            seen["pinned"] |= set(h["pinned"])
        for s in d["inputs"]["actuator_state"].values():
            if s:
                seen["actuator_state"] |= set(s)
        status.add(d["status"])
        lm_types.add(type(d["inputs"]["light_model"]).__name__)
    bad = 0
    for name, keys in seen.items():
        missing = sorted(CANON[name] - keys)
        extra = sorted(keys - CANON[name])
        ok = not missing
        bad += not ok
        print("%s %s keys %s; canon names missing: %s; non-canon: %s" % ("PASS" if ok else "FAIL", name, sorted(keys),
                                                                        missing, extra))
    odd = sorted(applied_status - APPLIED_STATUS)
    print("%s applied_scene statuses %s; outside the canon enum: %s" % ("FAIL" if odd else "PASS", sorted(applied_status), odd))
    odd_s = sorted(status - DECISION_STATUS)
    print("%s Decision.status values %s; outside the canon enum: %s" % ("FAIL" if odd_s else "PASS", sorted(status), odd_s))
    print("%s inputs.light_model is %s (rung 10's score.Dec compares it with a version string)"
          % ("FAIL" if lm_types != {"str"} else "PASS", sorted(lm_types)))
    print("RESULT %d decide decisions checked; %s" % (len(decided), "DEFECT REPRODUCED: non-canon Decision fields"
                                                       if bad or odd or odd_s or lm_types != {"str"} else "canon"))


if __name__ == "__main__":
    main()
