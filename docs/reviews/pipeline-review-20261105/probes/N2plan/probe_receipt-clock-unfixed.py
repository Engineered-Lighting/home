"""probe_receipt-clock-unfixed.py - N2 plan: is the receipt's clock tied to the event clock?

What it checks (synthetic data only, no network, writes nothing):
 1. Plan text. Lists every line of docs/plans/n2-need-test/PLAN.md that names
    observed_at, valid_until, "observed time", recv_pc_unix or started_unix, and
    says whether any line fixes the clock that the receipt's observed_at (and so
    its valid_until = observed_at + 10 s) is written on. The event clock is fixed
    there: t = floor(started_unix) + r + S.
 2. Mechanism, with the checkout's own qwen_scene.build_observation/first_receipt:
    the same slot's receipt built with frames' observed_at on
      A  floor(started_unix) + t_s   (the plan's event axis)
      B  started_unix + t_s          (capture clock, not floored)
      C  recv_pc_unix                (arrival wall clock; CHECK-V1: reported only)
    and how far each valid_until lies past the event-axis validity end.
 3. Consequence. A synthetic gold-fine block with three hold slots in a row,
    P = 4 s, S = 1.25 s: the scored seconds the plan's 4.1 rule ("fresh while the
    reply is inside its valid_until") calls fresh under A, B and C, and whether
    the 5 'stale on arrival' rule flips for a slow reply (S = 10.3 s).

Run:  python3 probe_receipt-clock-unfixed.py <root of the claude/plan-n2 checkout>
"""
import json
import math
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
plan = root / "docs/plans/n2-need-test/PLAN.md"
lines = plan.read_text(encoding="utf-8").split("\n")
names = re.compile(r"observed_at|valid_until|observed time|recv_pc_unix|started_unix")
print("== 1. plan lines naming a receipt or take clock")
hits = [(n, l) for n, l in enumerate(lines, 1) if names.search(l)]
for n, l in hits:
    print("  PLAN.md:%d: %s" % (n, l.strip()))
fixing = [(n, l) for n, l in hits
          if re.search(r"observed_at|valid_until|observed time", l) and re.search(r"started_unix|t_s|whole seconds", l)]
print("RESULT plan lines tying observed_at/valid_until to the event clock: %d" % len(fixing))
print("RESULT any line naming observed_at at all: %s" % any("observed_at" in l for _, l in hits))

sys.dont_write_bytecode = True  # never leave a __pycache__ in the checkout under test
sys.path.insert(0, str(root / "stack/services/perception-lab"))
import qwen_scene as qs  # noqa: E402

SU = 1793066600.73          # take.json started_unix (synthetic)
D0 = 0.30                   # first kept frame received 0.30 s after started_unix
LAT = 0.0                   # extra latency of later frames vs the first (constant here)
P, S = 4.0, 1.25
FRAME_DT = 0.1              # 10 frames per second on t_s
MAX_AGE = qs.MAX_AGE_SECONDS


def latest_at_or_before(r):
    return math.floor(r / FRAME_DT + 1e-9) * FRAME_DT - 0.05  # frame phase 0.05 s before the grid


CLOCKS = {
    "A floor(started_unix)+t_s": lambda ts: math.floor(SU) + ts,
    "B started_unix+t_s": lambda ts: SU + ts,
    "C recv_pc_unix": lambda ts: SU + D0 + LAT + ts,
}
FACTS = {"people_visible": "one", "subject_posture": "sitting", "held_object": "other", "book_state": "not_visible",
         "gaze_target": "elsewhere", "typing_visible": "no", "screen_facing_subject": "not_visible",
         "activity_hypothesis": "other", "visible_basis": ["person_visible"], "change_vs_prior": "no_prior"}


def receipt(r, clock, asked_unix=1793500000.0):
    t_latest = latest_at_or_before(r)
    ts = [t_latest - 3.0, t_latest - 1.5, t_latest]
    frames = [{"media_id": "m%d" % k, "frame_id": "f%d" % k, "observed_at": clock(t)} for k, t in enumerate(ts)]
    draft = qs.build_observation(
        site_id="site", camera_id="den", session_id="s", request_id="r%.1f" % r, frames=frames,
        generated_at=asked_unix, prompt_version=qs.PROMPT_VERSION, context=qs.model_context("evening 20:00"),
        body_sha256="0" * 64, engine_epoch="e", artifact_sha256="a" * 64, content=json.dumps(FACTS),
        finish_reason="stop", prior_supplied=False)
    return qs.first_receipt(draft, asked_unix + 0.01), t_latest


print("== 2. one slot (r = 32 s on t_s), valid_until vs the event-axis validity end")
for name, clock in CLOCKS.items():
    rec, t_latest = receipt(32.0, clock)
    end_axis = math.floor(SU) + t_latest + MAX_AGE
    print("  %-26s status=%s valid_until - event-axis end = %+.3f s" % (name, rec["status"], rec["valid_until"] - end_axis))

print("== 3. gold fine t_s [20, 80); replies set a fine level each slot, except holds at r = 36, 40, 44")
holds = {36.0, 40.0, 44.0}
scored = [k for k in range(26, 80)]  # past the 6 s grace
results = {}
for name, clock in CLOCKS.items():
    setters = []  # (event t, valid_until) of each non-hold reply, in arrival order
    r = P
    while r < 80:
        if r >= 20 and r not in holds:
            rec, _ = receipt(r, clock)
            setters.append((math.floor(SU) + r + S, rec["valid_until"]))
        r += P
    fresh = 0
    for k in scored:
        t = math.floor(SU) + k + 0.5
        known = [v for (arr, v) in setters if arr <= t]
        if known and t < known[-1]:
            fresh += 1
    results[name] = fresh
    print("  %-26s fresh fine seconds %d of %d  fine recall %.3f" % (name, fresh, len(scored), fresh / len(scored)))
extra = {n: results[n] - results["A floor(started_unix)+t_s"] for n in results}
print("RESULT extra seconds counted fresh vs the event axis: %s" % extra)

print("== 3b. a slow reply, S = 10.3 s, at r = 52: the plan's 'stale (past valid_until on arrival)'")
flips = {}
for name, clock in CLOCKS.items():
    rec, _ = receipt(52.0, clock)
    arrival = math.floor(SU) + 52.0 + 10.3
    flips[name] = "stale" if arrival >= rec["valid_until"] else "fresh"
    print("  %-26s -> %s" % (name, flips[name]))
same = len(set(flips.values())) == 1
print("RESULT the same reply is %s across clocks" % ("classified the same" if same else "stale on A but fresh on another clock"))
print("FAIL seam open: the receipt clock is not fixed by the plan and the choice changes fresh/stale"
      if (not fixing and not same and any(extra.values())) else "PASS no clock-dependent difference found")
