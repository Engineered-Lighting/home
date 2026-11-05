#!/usr/bin/env python3
"""verify_session-splits-at-midnight: verifier's reproduction of finding session-splits-at-midnight (rung 10).

What it checks (synthetic only, no network, nothing written anywhere; run it
with -B so no bytecode lands in the checkout): den-verify's hit-rate report
resamples "sessions" that are keyed by the local calendar day of each verify
frame (score.py `counted_by_day[self.clock.day(frame.ts)]`). It scores the same
synthetic evening of 12 one-point Hue decisions, 20 minutes apart, at UTC
offset -7 h, placed three ways:
  A  one evening 21:00 -> 00:40 local (crosses midnight)
  B  the identical evening shifted 3 h earlier, 18:00 -> 21:40 (same day)
  C  two separate sessions on one local day: 6 decisions at 08:00 and
     6 at 20:00 (the morning ones are the evening's first six)
and prints hit_rate.sessions, the interval and interval_status for each.
Owner choice 5 ("at least 3 sessions over at least 2 days") treats sessions and
days as different units, so A should be one session and C two.
FAIL lines mark A counted as 2 sessions (an interval from one evening) and C
counted as 1 (no interval although there are two sessions).

Run:
    python3 -B verify_session-splits-at-midnight.py <root of the rung-10-verify checkout>
"""
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(root / "tools" / "den-verify"))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

B0 = syn.HUE[0]
M = method.parse(method.closed_copy(method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md"))).scored()
MODEL = syn.hue_model({"p": {B0: 100.0}}, kelvins=(2700,))
CLOCK = Clock(syn.UTC_OFFSET_S)
LOCAL_MIDNIGHT = syn.T0 - (19 * 3600 + 3 * 60 + 29)   # T0 is 19:03:29 local
assert CLOCK.hhmmss(LOCAL_MIDNIGHT) == "000000", CLOCK.hhmmss(LOCAL_MIDNIGHT)

# 12 decisions; residual 0 is a hit, 0.3 a miss: 8 of the first 9 hit, 1 of the last 3
RESIDUALS = [0, 0, 0, 0.3, 0, 0, 0, 0, 0, 0.3, 0.3, 0]


def episode(i, t, r):
    ref = syn.light_state(t, {"p": 0.0}, anchored=False, ident=f"ls-ref-{i}")
    dec = syn.hue_decision(f"d-{i:02d}", t + 0.5, {B0: 0.0},
                           {B0: {"level": 1.0, "steps": [{"kind": "send", "sent_at": t + 0.5}]}},
                           f"ls-ref-{i}", targets={"A": {"surface_id": "p"}})
    after = syn.light_state(t + 4, {"p": round(100.0 * (1 + r), 9)}, anchored=False, ident=f"ls-after-{i}")
    return dec, [ref, after]


def score(starts):
    decs, frames = [], []
    for i, (t, r) in enumerate(zip(starts, RESIDUALS)):
        d, f = episode(i, t, r)
        decs.append(d)
        frames += f
    _, _, rep = Run(M, LightModel(MODEL), decs, frames, clock=CLOCK).run()
    return rep["hit_rate"], rep["decisions"]["verified"]


def at(h, m):
    return LOCAL_MIDNIGHT + h * 3600 + m * 60


bad = 0
a_starts = [at(21, 0) + 1200 * i for i in range(12)]
b_starts = [at(18, 0) + 1200 * i for i in range(12)]
c_starts = [at(8, 0) + 1200 * i for i in range(6)] + [at(20, 0) + 1200 * i for i in range(6)]
for name, starts in (("A evening 21:00-00:40", a_starts), ("B same evening 18:00-21:40", b_starts),
                     ("C two sessions 08:00 and 20:00, one day", c_starts)):
    hr, verified = score(starts)
    span = f"{CLOCK.hhmmss(starts[0])}..{CLOCK.hhmmss(starts[-1] + 4)}"
    days = sorted({CLOCK.day(t + 4) for t in starts})
    print(f"RESULT {name}: local {span}, calendar days {days}, verified {verified}/12, hits {hr['hits']} "
          f"misses {hr['misses']} value {hr['value']} verdict {hr['verdict']} | sessions {hr['sessions']} "
          f"interval {hr['interval']} status {hr['interval_status']!r}")
    if name.startswith("A") and hr["sessions"] != 1:
        bad += 1
        print(f"FAIL one evening session that crosses local midnight is resampled as {hr['sessions']} sessions "
              f"and gets an interval {hr['interval']} labelled {hr['interval_status']!r}")
    if name.startswith("B") and hr["sessions"] != 1:
        bad += 1
        print("FAIL the same evening before midnight is not one session")
    if name.startswith("C") and hr["sessions"] != 2:
        bad += 1
        print(f"FAIL two sessions on one local day are merged into {hr['sessions']} session(s): "
              f"interval {hr['interval']}")
# A2: evening A again, but frames carry no `id` key (LightState has none in the
# contract), so the tool names them ls-HHMMSS itself and decisions refer to that name
decs, frames = [], []
for i, (t, r) in enumerate(zip(a_starts, RESIDUALS)):
    d, f = episode(i, t, r)
    for fr in f:
        fr.pop("id", None)
    d["inputs"]["light_state"] = "ls-" + CLOCK.hhmmss(t)
    decs.append(d)
    frames += f
_, _, rep = Run(M, LightModel(MODEL), decs, frames, clock=CLOCK).run()
hr = rep["hit_rate"]
print(f"RESULT A2 evening 21:00-00:40 with dateless derived ids: verified {rep['decisions']['verified']}/12, "
      f"sessions {hr['sessions']} interval {hr['interval']} status {hr['interval_status']!r}")
if hr["sessions"] != 1:
    bad += 1
    print("FAIL with contract-shaped frames (no id), one evening crossing midnight still yields a 2-session interval")
print(f"RESULT {bad} session-unit mismatch(es)")
