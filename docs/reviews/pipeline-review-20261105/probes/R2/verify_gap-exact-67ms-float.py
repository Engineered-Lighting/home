#!/usr/bin/env python3
"""verify_gap-exact-67ms-float: how observe.depth_state treats gaps at and near 67 ms on real-shaped timestamps.

What it checks (independent re-run of finding gap-exact-67ms-float)
    1. observe.depth_state with the default METHOD.md (max_depth_gap_s 0.067)
       on microsecond-quantised host timestamps (the installed camera script
       writes timedelta.total_seconds(), so real stamps are whole microseconds)
       whose gap is exactly 67,000 us: what fraction come out 'too_old'?
       (If the float rounding goes both ways, the boundary is a coin toss.)
    2. The window between one stereo frame at 15 fps (66,667 us, notes.json)
       and 67,000 us: what the code says there.
    3. Gaps up to 20 ms (the installed script's Sync threshold): all 'used'?

How to run
    python3 -B verify_gap-exact-67ms-float.py <branch checkout root>

Synthetic numbers only; nothing is written and nothing connects anywhere.
"""
import array
import random
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-observe"))
import method as method_file  # noqa: E402
import observe  # noqa: E402
from takes import Depth  # noqa: E402

settings = observe.Settings(method_file.load(root / "tools" / "den-observe" / "METHOD.md"))
mm = array.array("H", bytes(640 * 400 * 2))
mm[5] = 1000
depth = Depth(640, 400, mm, 0.0, 1)


def state(t_rgb_us, gap_us):
    t_rgb = t_rgb_us / 1e6
    t_depth = (t_rgb_us - gap_us) / 1e6
    return observe.depth_state({"frame": {"capture_ts": t_rgb}}, {"t_depth": t_depth}, depth, settings)[0]


rng = random.Random(7)
N = 20000
for label, lo, hi in (("host-monotonic ~1e3..1e5 s", 1_000_000_000, 100_000_000_000),
                      ("host-monotonic ~1e5..1e6 s", 100_000_000_000, 1_000_000_000_000)):
    old = sum(state(rng.randrange(lo, hi), 67_000) == "too_old" for _ in range(N))
    print(f"RESULT exact 67,000 us gap, {label}: too_old {old} of {N} ({old / N:.1%}), used {N - old}")

window = [66_700, 66_800, 66_900, 66_999]
print("RESULT gaps above one 15 fps stereo frame (66,667 us) but under 67,000 us: "
      + ", ".join(f"{g} us -> {state(5_000_250_000, g)}" for g in window))

synced = {state(rng.randrange(1_000_000_000, 100_000_000_000), rng.randrange(-20_000, 20_001)) for _ in range(N)}
print(f"RESULT gaps within the 20 ms Sync threshold: states seen {sorted(synced)}")
