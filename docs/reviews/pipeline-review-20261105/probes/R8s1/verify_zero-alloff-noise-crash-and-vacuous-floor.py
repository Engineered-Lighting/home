#!/usr/bin/env python3
"""verify_zero-alloff-noise-crash-and-vacuous-floor: how many zero-noise blocks does the score.json crash
need? score() appends float('inf') to a step's SNR list for each block whose all-off noise is 0, but
stores only statistics.median(snr) per hold-out step, so inf reaches score.json only when at least half
of some hold-out step's counted pairs come from zero-noise blocks. This probe sets the all-off spread to
0 for a growing share of blocks (synthetic readings, no frames) and tries json.dumps(allow_nan=False) on
the score, exactly as model.write_new does. It also re-checks frames_per_capture = 1 through
reduce_captures (one frame -> spread 0.0 for every block).

    python3 -B verify_zero-alloff-noise-crash-and-vacuous-floor.py <root of the claude/rung-8-stage1 checkout>
"""
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import model  # noqa: E402
import synthetic  # noqa: E402
import test_stage1 as T  # noqa: E402

m = T.method1()
den = synthetic.SyntheticDen(block_px=40, daylight=0.004, drift=0.0, noise=2e-5)
for share in (0.0, 0.25, 0.45, 0.55, 0.75, 1.0):
    r = T.readings_for(den, m)
    zero = set(range(int(share * r["blocks"])))
    for s in r["steps"]:
        if s["kind"] == "all_off":
            s["spread"] = [0.0 if b in zero else v for b, v in enumerate(s["spread"])]
    fitted = model.fit(r, r["temperatures"], m)
    scored = model.score(r, fitted, m)
    infs = sum(1 for st in scored["information_only"]["steps"] if st["median_snr"] == float("inf"))
    try:
        json.dumps(scored, allow_nan=False)
        ok = "writes"
    except ValueError as exc:
        ok = f"ValueError: {exc}"
    print(f"RESULT zero-noise blocks {len(zero):3d}/{r['blocks']}: hold-out steps with median_snr inf {infs:2d}; "
          f"score.json {ok}; verdict {scored['error']['verdict']}")
# frames_per_capture = 1: reduce_captures gives spread 0.0 for every block
m1 = T.method1(frames_per_capture=1)
owner = model.pixel_blocks(m1)
caps = [{"step": {"index": 1, "kind": "all_off", "scene": model.off_scene()}, "t": 0.0,
         "frames": [([0.01] * len(owner), [False] * len(owner))]}]
r1 = model.reduce_captures(caps, m1, owner)
print("RESULT frames_per_capture=1 spreads:", sorted(set(r1["steps"][0]["spread"])))

# Black blocks: zero spread AND the same all-off reading in every all-off capture (every pixel at code 0
# in every all-off frame), the reporter's after-dark scenario. Lit steps keep their signal.
print("RESULT --- black blocks (constant all-off reading and zero spread) ---")
for share in (0.25, 0.45, 0.55, 0.75, 1.0):
    r = T.readings_for(den, m)
    black = set(range(int(share * r["blocks"])))
    offs = [s for s in r["steps"] if s["kind"] == "all_off"]
    for s in offs:
        s["reading"] = [0.0 if b in black else v for b, v in enumerate(s["reading"])]
        s["spread"] = [0.0 if b in black else v for b, v in enumerate(s["spread"])]
    fitted = model.fit(r, r["temperatures"], m)
    scored = model.score(r, fitted, m)
    infs = sum(1 for st in scored["information_only"]["steps"] if st["median_snr"] == float("inf"))
    try:
        json.dumps(scored, allow_nan=False)
        ok = "writes"
    except ValueError as exc:
        ok = f"ValueError: {exc}"
    print(f"RESULT black blocks {len(black):3d}/{r['blocks']}: hold-out steps with median_snr inf {infs:2d}; "
          f"score.json {ok}; verdict {scored['error']['verdict']}")
