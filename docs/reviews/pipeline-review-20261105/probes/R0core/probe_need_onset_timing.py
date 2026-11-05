"""probe_need_onset_timing: need.py's onset and dwell timing on constructed cases, against an independent
hand computation of the owner's onset protocol (owner-choices item 3: onset = first touch, timed to the first output
that leaves 'none'; the 5 s dwell reported separately; 3 s median target).

Case: A's first touch (contact) is at T0. Qwen samples are captured every CADENCE s, each arriving LATENCY s after
capture; captures before T0 show A resting (idle), captures at or after T0 show A reading (playing). For each case
the probe computes by hand:
  first_output_t = arrival of the first capture at or after T0           (first record leaving 'none', 'starting')
  onset latency  = first_output_t - T0                                   (owner protocol)
  engaged_t      = arrival of the second reading capture                 (den_lighting's 2-sample dwell, need-v0)
  dwell_5s_t     = first reading capture + 5 s                           (A7's 5 s entry dwell, owner kept)
and compares with need.make_policy() fed the same facts (built with the perceiver's own builders).

How to run (synthetic data only; no network):
    python3 -B probe_need_onset_timing.py /path/to/den-core-checkout
"""
import json
import math
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-core"))
import need  # noqa: E402
import synthetic  # noqa: E402
from sources import fact_time  # noqa: E402

T0 = 1793764800.0
REST = synthetic.TEMPLATES["rest"]
READ = synthetic.TEMPLATES["reading"]
bad = 0


def case(cadence, latency, phase):
    captures = [T0 + phase + cadence * k for k in range(-3, 6)]
    evs = []
    for n, c in enumerate(captures):
        line = json.loads(synthetic.fact_line(n, c, c + latency, facts=READ if c >= T0 else REST))
        evs.append({"schema": "den-event-1", "t": fact_time(line["available_at"]), "seq": n, "kind": "facts",
                    "source": "synthetic", "data": line})
    policy, out = need.make_policy(), []
    for e in evs:
        for o in policy.handle(e):
            out.append((e["t"], o["data"]["focused_engagement"]))
    reading = [c for c in captures if c >= T0]
    # Hand values (the fact log keeps milliseconds, so round the arrival the same way).
    arrive = [math.floor((c + latency) * 1000 + 0.5) / 1000 for c in reading]
    hand = {"first_output_t": arrive[0], "onset_latency": arrive[0] - T0, "onset_ts": reading[0],
            "engaged_t": arrive[1], "dwell_5s_t": reading[0] + 5.0}
    first = next(((t, fe) for t, fe in out if fe["state"] != "none"), None)
    engaged = next(((t, fe) for t, fe in out if fe["state"] == "engaged"), None)
    tool = {"first_output_t": first[0] if first else None, "first_state": first[1]["state"] if first else None,
            "onset_ts": first[1]["onset_ts"] if first else None, "engaged_t": engaged[0] if engaged else None}
    return hand, tool


for cadence, latency, phase in ((4.0, 1.2, 0.5), (2.0, 0.8, 0.0), (8.0, 1.97, 3.0), (4.0, 3.5, 3.9)):
    hand, tool = case(cadence, latency, phase)
    same = (abs(tool["first_output_t"] - hand["first_output_t"]) < 1e-6 and tool["first_state"] == "starting"
            and abs(tool["onset_ts"] - hand["onset_ts"]) < 1e-3 and abs(tool["engaged_t"] - hand["engaged_t"]) < 1e-6)
    print("RESULT cadence=%.1fs latency=%.2fs: first non-none at T0%+.3f (%s; hand T0%+.3f) onset latency %.3f s "
          "(target 3 s median); engaged at T0%+.3f (hand T0%+.3f), i.e. %.1f s after the first output; a 5 s dwell "
          "from the first reading capture would engage at T0%+.3f" % (
              cadence, latency, tool["first_output_t"] - T0, tool["first_state"], hand["first_output_t"] - T0,
              tool["first_output_t"] - T0, tool["engaged_t"] - T0, hand["engaged_t"] - T0,
              tool["engaged_t"] - tool["first_output_t"], hand["dwell_5s_t"] - T0))
    if same:
        print("PASS: need's first output leaving none and its engaged move match the hand computation")
    else:
        bad += 1
        print("FAIL: mismatch tool=%s hand=%s" % (tool, hand))
print("RESULT: %s" % ("PASS" if not bad else "FAIL (%d)" % bad))
sys.exit(1 if bad else 0)
