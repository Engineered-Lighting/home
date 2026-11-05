#!/usr/bin/env python3
"""Verifier probe for finding session-splits-at-midnight (den-verify, rung 10).

What it checks: one 40-minute evening session with two scene changes, scored
in one run (the README's per-session folder). Case 1 runs 23:40-00:20 local,
across midnight; case 2 is the same session three hours earlier (20:40-21:20);
case 3 puts a morning session and an evening session of the same day in one
run. The report's hit_rate.sessions and interval should not depend on where
local midnight falls. Synthetic records only; no network; writes nothing.

Run:
    python3 verify_intent_session-splits-at-midnight.py <root of the rung-10-verify checkout>
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-verify"))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

B0 = syn.HUE[0]
M = method.parse(method.closed_copy(method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md"))).scored()
NAMES = [f"p{i:02d}" for i in range(10)]
MODEL = syn.hue_model({n: {B0: 100.0} for n in NAMES}, kelvins=(2700,))
OFF = syn.UTC_OFFSET_S
DAY = 86400


def local_midnight_after(t):
    return ((t + OFF) // DAY + 1) * DAY - OFF


def change(did, t, hits):
    """A 0 -> full change at t; `hits` of the 10 points land within 15%, the rest 30% high."""
    ref = syn.light_state(t, {n: 0.0 for n in NAMES}, anchored=False, ident=f"ls-ref-{did}")
    dec = syn.hue_decision(did, t + 0.5, {B0: 0.0}, {B0: {"level": 1.0, "steps": [
        {"kind": "send", "sent_at": t + 0.5}]}}, f"ls-ref-{did}",
        targets={f"T{i}": {"surface_id": n} for i, n in enumerate(NAMES)})
    after = syn.light_state(t + 4, {n: (100.0 if i < hits else 130.0) for i, n in enumerate(NAMES)},
                            anchored=False)
    return [dec], [ref, after]


def run(changes):
    decs, frames = [], []
    for did, t, hits in changes:
        d, f = change(did, t, hits)
        decs += d
        frames += f
    _, _, rep = Run(M, LightModel(MODEL), decs, frames, clock=Clock(OFF)).run()
    return rep["hit_rate"]


mid = local_midnight_after(syn.T0)
clk = Clock(OFF)
cases = {
    "one session 23:40-00:20": [("d-a", mid - 1200, 9), ("d-b", mid + 1200, 6)],
    "same session 20:40-21:20": [("d-a", mid - 1200 - 3 * 3600, 9), ("d-b", mid + 1200 - 3 * 3600, 6)],
    "two sessions, 09:00 and 21:00": [("d-a", mid - 15 * 3600, 9), ("d-b", mid - 3 * 3600, 6)],
}
out = {}
for name, ch in cases.items():
    hr = run(ch)
    out[name] = hr
    times = ", ".join(clk.hhmmss(t) for _, t, _ in ch)
    print(f"RESULT {name:30} local {times}: sessions={hr['sessions']} hits={hr['hits']}/{hr['hits'] + hr['misses']} "
          f"interval={hr['interval']} status={hr['interval_status']!r}")
a, b = out["one session 23:40-00:20"], out["same session 20:40-21:20"]
if a["sessions"] == 2 and b["sessions"] == 1:
    print("FAIL one evening session counts as 2 bootstrap sessions when it crosses local midnight, and gets a "
          f"'session-bootstrap' interval {a['interval']}; the same session earlier has none")
c = out["two sessions, 09:00 and 21:00"]
if c["sessions"] == 1:
    print("RESULT two separate sessions on one calendar day are merged into one bootstrap unit (conservative)")
print(f"RESULT verdicts: {[o['verdict'] for o in out.values()]} (the verdict is the pooled point estimate)")
