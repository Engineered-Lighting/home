"""tiebreak_stage0-owner-light-step-learned: tiebreak verifier probe for finding stage0-owner-light-step-learned.

What it checks (synthetic data only, temp dirs only, no host, no network):
  A. Text facts on the branch under test (claude/rung-8-stage0): README step 5 tells the owner to change a
     bulb in the HA app (no bulb on, or the "too dark" refusal); sweep.py's too-dark message says the same;
     the README says sweep.py writes no lights.
  B. Spec and plan facts: ladder rung 7's probe rule is about probes logged as NON-HUMAN changes; rung 8's
     "same probe rule as rung 7" sits in its tested field for held-out mixed scenes; the rung 8 plan says
     "No pause" for Stage 0 and documents that den_lighting keeps the owner's change as its normal scene;
     the plan's corrections exclusion covers session.json windows only; cph-budget-v0 is a planning value,
     not a gate; rung 11's gate re-estimates corrections in both canary arms.
  C. The mechanism, through correction-capture's real capture(): an owner HA-app change (user id present)
     while den_lighting is automatic with occupancy on is counted as a people=True 'app' correction; the
     optional set-back is a second one; the same change made from outside the den (occupancy off) is not
     counted (outside_exposure); without the owner's changes nothing is counted.
  D. What the extra counts can change: cph-budget-v0's planning verdict, via capture.verdict(), at an
     edge count; rung 11's gate is not fed by this export.

Run:
  python3 -B tiebreak_stage0-owner-light-step-learned.py <rung-8-stage0 checkout> [<correction-capture
      checkout> [<plan-rung-8 checkout> [<live-records checkout>]]]
  (defaults: the sibling folders correction-capture, plan-rung-8 and live-records next to the first arg)

Prints PASS/FAIL/RESULT lines. PASS means the stated fact holds at the pinned commits.
"""
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

stage0 = Path(sys.argv[1]).resolve()
cc_root = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else stage0.parent / "correction-capture"
plan_root = Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else stage0.parent / "plan-rung-8"
spec_root = Path(sys.argv[4]).resolve() if len(sys.argv) > 4 else stage0.parent / "live-records"

fails = 0


def check(cond, what):
    global fails
    print(("PASS " if cond else "FAIL ") + what)
    if not cond:
        fails += 1


def flat(path):
    return " ".join(path.read_text("utf-8").split())


# ---------------------------------------------------------------- A. branch text
readme = flat(stage0 / "tools" / "den-light" / "README.md")
sweep = flat(stage0 / "tools" / "den-light" / "sweep.py")
check("If none is on, turn one on in the HA app; den_lighting keeps that as its normal scene" in readme,
      "A1 README step 5: owner turns a bulb on in the HA app, and is told den_lighting keeps it as normal")
check("turn on one more den bulb (front left, front right, rear left or rear right) at full in the HA app" in readme,
      "A2 README step 5: on 'too dark' the owner turns one more bulb on at full in the HA app")
check("too dark" in sweep and "HA app" in sweep, "A3 sweep.py's too-dark refusal names the HA app step")
check("never to Home Assistant, and writes no lights" in readme, "A4 README: sweep.py writes no lights")

# ---------------------------------------------------------------- B. spec and plan
ladder = json.loads((spec_root / "docs" / "architecture" / "live-v1" / "ladder.json").read_text("utf-8"))
rungs = {r["n"]: r for r in ladder["rungs"]}
check("every probe logged as a non-human change and excluded from corrections" in rungs[7]["tested"],
      "B1 ladder rung 7: the probe rule excludes probes logged as NON-HUMAN changes")
check(rungs[8]["tested"].startswith("Held-out mixed scenes") and "same probe rule as rung 7" in rungs[8]["tested"],
      "B2 ladder rung 8: 'same probe rule as rung 7' qualifies the held-out mixed-scene test")
check("canary re-estimates it in both arms" in rungs[11]["gate"] and "planning value" in rungs[11]["gate"],
      "B3 ladder rung 11: the pre-canary incumbent log is a planning value; the gate re-estimates in both arms")
plan = flat(plan_root / "docs" / "plans" / "rung-8-light-model" / "PLAN.md")
check("No pause. The tool writes no lights. If no bulb is on, the owner turns one on in the HA app, and "
      "den_lighting keeps that as its normal scene until it is changed again" in plan,
      "B4 plan approval 1: 'No pause', and den_lighting learning the owner's change is in the approved text")
check("mark holds inside that window `probe_session`" in plan and "session.json" in plan,
      "B5 plan: the corrections exclusion is keyed on session.json windows (Stage 1 only)")
rule = json.loads((cc_root / "tools" / "correction-capture" / "cph-budget-v0.json").read_text("utf-8"))
check("It is not a promotion gate and decides nothing about the coordinator" in rule["what_it_is"],
      "B6 cph-budget-v0: a planning value, not a gate")
check("Rung 11 hands the den's four bulbs to a new coordinator only if it needs fewer hand corrections"
      in flat(cc_root / "tools" / "correction-capture" / "README.md"),
      "B7 correction-capture README: it counts how often people correct den_lighting by hand")

# ---------------------------------------------------------------- C. the mechanism through capture()
sys.path.insert(0, str(cc_root / "tools" / "correction-capture"))
import capture  # noqa: E402
import test_capture as tc  # noqa: E402  the tool's own synthetic export writer (three recorder formats)

L0, L1, L2, L3 = capture.LIGHTS  # L2 is the rear left bulb
T0 = datetime(2026, 11, 6, 3, 0, tzinfo=timezone.utc).timestamp()
DIM = {"on": True, "brightness": 40, "kelvin": 2700}
FULL = {"on": True, "brightness": 254, "kelvin": 2700}


def evening(owner_changes, owner_in_den=True):
    den = tc.Den(start=T0, occupancy="on" if owner_in_den else "off")
    for i, e in enumerate(capture.LIGHTS):  # den_lighting's dim arrival scene, its own commands
        den.command(T0 + 5 + i, e, DIM)
    if owner_changes:
        # step 5: 'too dark' -> one more bulb at full in the HA app (hold opens, bulb row carries the user id)
        den.hold(T0 + 300, {L2: FULL})
        den.bulb(T0 + 300.3, L2, True, user=True)
    # step 6: the owner steps out of view for the sweep, comes back within 120 s (hold kept)
    if owner_in_den:
        den.row(T0 + 340, occupancy="off")
        den.row(T0 + 450, occupancy="on")
    if owner_changes:
        # 'set it back by hand afterwards if you want'
        den.hold(T0 + 600, {L2: DIM})
        den.bulb(T0 + 600.3, L2, True, user=True)
    den.row(T0 + 3600)
    return den


def run(den):
    tmp = Path(tempfile.mkdtemp(prefix="tb-stage0-"))
    try:
        record, _ = capture.capture(*den.write(tmp))
    finally:
        shutil.rmtree(tmp, True)
    return record


rec = run(evening(True))
counted = [(c["source"], c["people"]) for c in rec["corrections"]]
print("RESULT owner in den, automatic, occupancy on: counted %s; not_counted reasons %s"
      % (counted, sorted({c.get("reason") for c in rec["not_counted"]})))
check(counted == [("app", True), ("app", True)],
      "C1 the owner's step-5 change and the set-back are two people=True 'app' corrections (mechanism holds)")
rec_out = run(evening(True, owner_in_den=False))
print("RESULT owner outside the den (occupancy off): counted %s; not_counted %s"
      % ([c["source"] for c in rec_out["corrections"]], [c.get("reason") for c in rec_out["not_counted"]]))
check(rec_out["corrections"] == [], "C2 the same change made with occupancy off is not counted (outside_exposure)")
rec_none = run(evening(False))
check(rec_none["corrections"] == [], "C3 control: without the owner's changes nothing is counted")

# ---------------------------------------------------------------- D. what 1-2 extra counts can move
consts = rule["constants"]
flip = None
for hours in (40.0, 60.0, 100.0):
    for k in range(0, 200):
        if (capture.verdict(hours, k, k, consts) == "undetermined"
                and capture.verdict(hours, k + 2, k + 2, consts) == "within_budget"):
            flip = (hours, k)
            break
    if flip:
        break
if flip:
    print("RESULT edge case: at %.0f occupied h, %d -> %d people actions moves cph-budget-v0's PLANNING verdict "
          "undetermined -> within_budget" % (flip[0], flip[1], flip[1] + 2))
else:
    print("RESULT no edge flip found in the searched range")
print("RESULT rung 11's gate is measured in the canary's own arms (B3), so no pass/fail verdict reads this export")
print("RESULT %d check(s) failed" % fails if fails else "RESULT all checks hold")
sys.exit(1 if fails else 0)
