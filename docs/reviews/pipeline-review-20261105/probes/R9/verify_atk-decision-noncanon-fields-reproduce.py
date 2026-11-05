"""verify_atk-decision-noncanon-fields-reproduce: feed den-core decide's Decision records to rung 10's own reader.

What it checks
  1. decide's Decisions (synthetic evening with a touch-hold and a game; need, incumbent, decide) use the canon
     field names of schemas.json's Decision: requested_scene {level, cct_k, ...}, applied_scene[].readback
     {level, cct_k, aim}, hold_set.held[].pinned {level_pct, cct_k, aim, source}, Decision.status enum
     applied|partial|superseded|kept_current, inputs.light_model a version string.
  2. The consequence: rung 10's score.Dec (tools/den-verify/score.py on claude/rung-10-verify), built on each
     decide Decision, reads before_state() and state_at() levels. A decide decision that requests lit bulbs
     (brightness > 0) should not read back as level 0 on every channel, and its light_model should not be
     flagged as a different model than the one loaded (loaded version = decide's own model version).
  den-verify is copied to a temp dir first and imported from there, with no bytecode written.

How to run (synthetic data only; temp dirs only; no host contacted):
  python3 -B verify_atk-decision-noncanon-fields-reproduce.py /path/to/den-core-live-checkout [/path/to/rung-10-verify]
"""
import shutil
import sys
import tempfile
import types
from pathlib import Path

DEFAULT_R10 = ("$SCRATCH/wt/"
               "rung-10-verify")


def main():
    sys.dont_write_bytecode = True
    root = Path(sys.argv[1]).resolve()
    r10 = Path(sys.argv[2] if len(sys.argv) > 2 else DEFAULT_R10).resolve()
    tmp = Path(tempfile.mkdtemp(prefix="verify-r9-canon-"))
    try:
        shutil.copytree(r10 / "tools" / "den-verify", tmp / "den-verify",
                        ignore=shutil.ignore_patterns("__pycache__"))
        sys.path.insert(0, str(root / "tools" / "den-core"))
        import runner
        import synthetic
        items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True, game=True)
        header, log = runner.run(runner.tagged(items, "synthetic"),
                                 runner.load_policies(["need", "incumbent", "decide"]), synthetic=True)
        decided = [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]
        print("INFO %d decide decisions" % len(decided))
        d0 = decided[0]
        b0 = sorted(d0["requested_scene"])[0]
        print("INFO first decision requested_scene[%s] = %s" % (b0, d0["requested_scene"][b0]))
        print("INFO first decision applied_scene[%s] = %s" % (b0, {k: v for k, v in d0["applied_scene"][b0].items()
                                                                    if k != "reason"}))
        print("INFO first decision status = %r; inputs.light_model type = %s"
              % (d0["status"], type(d0["inputs"]["light_model"]).__name__))
        held_rows = [h for d in decided for h in d["inputs"]["hold_set"]["held"]]
        if held_rows:
            print("INFO a hold_set.held[].pinned = %s" % held_rows[0]["pinned"])

        # Rung 10's reader on these records.
        sys.path.insert(0, str(tmp / "den-verify"))
        import score as r10score  # rung 10's score.py, from the temp copy
        from records import Clock
        model_version = d0["inputs"]["light_model"]["version"]
        run = types.SimpleNamespace(frames_by_id={}, clock=Clock(utc_offset_s=0), first_ts=d0["ts"],
                                    m={"settle_margin_s": 0.5}, model=types.SimpleNamespace(version=model_version))
        zero_all, lit_requests, flagged = 0, 0, 0
        example = None
        for d in decided:
            dec = r10score.Dec(d, run)
            lit = [b for b, s in d["requested_scene"].items() if s.get("on") and (s.get("brightness") or 0) > 0]
            if not lit:
                continue
            lit_requests += 1
            state, held, _rev = dec.state_at(dec.ts, [])
            before = dec.before_state()
            if all(v["level"] == 0 for v in state.values()) and all(v["level"] == 0 for v in before.values()):
                zero_all += 1
            if dec.problem and "light model" in dec.problem:
                flagged += 1
            if example is None:
                example = (d["decision_id"], {b: d["requested_scene"][b] for b in lit[:1]},
                           {b: state[b] for b in lit[:1]}, {b: before.get(b) for b in lit[:1]}, dec.problem)
        print("INFO rung 10 on %s: requested %s -> state_at %s, before_state %s"
              % (example[0], example[1], example[2], example[3]))
        print("INFO rung 10 Dec.problem: %s" % example[4])
        print("%s rung 10 reads every channel of %d of %d lit decide decisions as level 0"
              % ("FAIL" if zero_all else "PASS", zero_all, lit_requests))
        print("%s rung 10 flags %d of %d lit decide decisions as using another light model (loaded %s, decide's own)"
              % ("FAIL" if flagged else "PASS", flagged, lit_requests, model_version))
        print("RESULT %s" % ("DEFECT REPRODUCED: rung 10 misreads decide's Decision dialect"
                             if zero_all or flagged else "rung 10 reads decide's Decisions"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
