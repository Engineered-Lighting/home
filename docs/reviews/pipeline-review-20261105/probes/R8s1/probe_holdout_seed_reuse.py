#!/usr/bin/env python3
"""probe_holdout_seed_reuse: a second probe session gets the same held-out scenes as the first.

What it checks: model.schedule() seeds its random hold-outs from METHOD-V1's schedule_seed alone. The
plan says a second session runs when "anything is tuned on its hold-outs"; that session's 12 random
hold-outs (and the gaming, all-full and split scenes) are then the very scenes the tuning looked at.
Nothing in probe.py or model.py derives a fresh seed per session or refuses a reused one.

How to run (pure functions, no I/O):
    python3 -B probe_holdout_seed_reuse.py <root of the claude/rung-8-stage1 checkout>
"""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import guard  # noqa: E402
import model  # noqa: E402

RANGES = {"light.den_front_left": (2000, 6535), "light.front_right": (2000, 6535),
          "light.den_rear_left": (2202, 6535), "light.den_rear_right": (2202, 6535)}
WARMEST = {e: lo for e, (lo, _) in RANGES.items()}
SNAP1 = {"light.den_front_left": guard.scene(True, 180, 2700), "light.front_right": guard.scene(False, 120, 3000),
         "light.den_rear_left": guard.scene(True, 90, 2500), "light.den_rear_right": guard.scene(True, 200, 4000)}
SNAP2 = {e: guard.scene(True, 150, 3300) for e in guard.LIGHTS}  # session 2: a different normal scene


def holdouts(snapshot):
    m = model.load_stage1().proposed()
    plan = model.schedule(m, snapshot, RANGES, WARMEST)
    return [(s["label"], s["scene"]) for s in plan["steps"] if s["kind"] == "holdout"]


def main():
    first, second = holdouts(SNAP1), holdouts(SNAP2)
    same = [a[0] for a, b in zip(first, second) if a == b]
    print("RESULT hold-outs per session:", len(first), len(second))
    print("RESULT hold-outs identical in session 1 and session 2:", len(same), same)
    if len(same) >= 12:
        print("FAIL: a second session re-scores", len(same), "of", len(first), "hold-out scenes that session 1 "
              "already showed; a second session cannot be a fresh hold-out after tuning on session 1's")
    else:
        print("PASS")


if __name__ == "__main__":
    main()
