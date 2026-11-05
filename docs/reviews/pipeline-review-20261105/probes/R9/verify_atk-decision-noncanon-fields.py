"""verify_atk-decision-noncanon-fields: feed den-core decide's Decision records to rung 10's own reader.

What it checks (finding atk-decision-noncanon-fields, verifier lens: intent and spec)
  Builds a synthetic evening (need, incumbent, decide; touch) with den-core's own runner, takes decide's Decision
  records, and hands each to rung 10's score.Dec (tools/den-verify on claude/rung-10-verify), which was written
  from schemas.json. Reports what rung 10 reads back: before_state() levels, state_at() levels for held and free
  channels, and whether Dec flags a light-model problem. Also counts decide decisions whose actuator_state holds a
  null (never observed) bulb, which rung 10's before_state() would dereference.

How to run (synthetic data only; nothing written; no host contacted):
  python3 -B verify_atk-decision-noncanon-fields.py /path/to/den-core-live-checkout /path/to/rung-10-verify-checkout
"""
import sys
import types
from pathlib import Path


def main():
    core, verify = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    sys.path.insert(0, str(core / "tools" / "den-core"))
    import runner
    import synthetic
    items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True)
    header, log = runner.run(runner.tagged(items, "synthetic"),
                             runner.load_policies(("need", "incumbent", "decide")), synthetic=True,
                             sources=[{"name": "synthetic:evening", "params": {"touch": True}}])
    decs = [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]
    print("INFO %d decide decisions" % len(decs))
    sys.path.insert(0, str(verify / "tools" / "den-verify"))
    import score as vscore
    clock = types.SimpleNamespace(resolve=lambda v, anchor: float(v))
    run = types.SimpleNamespace(frames_by_id={}, clock=clock, first_ts=decs[0]["ts"] if decs else 0.0,
                                m={}, model=types.SimpleNamespace(version="lm-test-1"))
    nulls = sum(1 for d in decs if any(v is None for v in d["inputs"]["actuator_state"].values()))
    print("INFO %d decide decision(s) carry a null actuator_state entry" % nulls)
    lit_read_zero = flagged = crashes = 0
    for d in decs:
        try:
            dec = vscore.Dec(d, run)
        except Exception as error:  # noqa: BLE001
            crashes += 1
            print("RESULT Dec() raised %s: %s" % (type(error).__name__, error))
            continue
        if dec.problem and "light model" in dec.problem:
            flagged += 1
        try:
            state, held, _ = dec.state_at(d["ts"], [])
        except Exception as error:  # noqa: BLE001
            crashes += 1
            print("RESULT state_at raised %s: %s" % (type(error).__name__, error))
            continue
        lit = [b for b, s in d["requested_scene"].items() if s["on"]]
        if lit and all(state.get(b, {}).get("level") == 0.0 for b in lit):
            lit_read_zero += 1
    print("RESULT %d of %d decisions: every lit channel read by rung 10 as level 0.0" % (lit_read_zero, len(decs)))
    print("RESULT %d of %d decisions flagged by rung 10 as another light model" % (flagged, len(decs)))
    print("RESULT %d crash(es) in rung 10's reader" % crashes)
    print("FAIL rung 10 misreads den-core decisions" if (lit_read_zero or flagged or crashes) else "PASS")


if __name__ == "__main__":
    main()
