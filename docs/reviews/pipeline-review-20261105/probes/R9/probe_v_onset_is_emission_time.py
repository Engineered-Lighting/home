#!/usr/bin/env python3
"""Probe: score.py's anticipation clock starts at the event time t of need's first PersonState that leaves none
(score.episodes), not at the onset the owner fixed (onset-protocol: contact-starting, onset = first touch), which the
PersonState itself carries as focused_engagement.onset_ts (need v0: the capture time of the first playing sample).
need's own detection latency (Qwen latency plus queueing) is therefore outside the anticipation window.

    python3 -B probe_v_onset_is_emission_time.py <den-core-live checkout root>

It runs a synthetic evening with need, incumbent and decide (as test_rung9 does, synthetic flag on, plumbing only),
lists each episode's emission t0, its onset_ts and decide's first reading-look decision, and rescores anticipation
with the window started at onset_ts (an independent reimplementation), then again with a 6 s detection latency
added to every fact line (observed_at unchanged, available_at later), showing how many episodes flip.
Synthetic data only, temp nothing, no host. Prints RESULT lines and PASS/FAIL.
"""
import os
import sys


def main():
    root = os.path.abspath(sys.argv[1])
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.join(root, "tools", "den-core"))
    import decide
    import light_model
    import runner
    import score
    import synthetic

    W, RISE = 10.0, 0.2

    def evening(delay):
        items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True, game=True)
        if delay:
            items = [(t + delay, k, d) if k == "facts" else (t, k, d) for t, k, d in items]
        return runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need", "incumbent", "decide"]),
                          synthetic=True, sources=[{"name": "synthetic:evening"}])

    flips_total = 0
    for delay in (0.0, 6.0):
        header, log = evening(delay)
        config = next(p["config"] for p in header["policies"] if p["id"] == "decide")
        model = light_model.LightModel(config["light_model"])
        people = {e["data"]["track_id"]: e["data"] for e in log if e["kind"] == "person_state"}
        arms, found = score.arms(header, log), score.episodes(log)
        tool = score.surrogates(arms["candidate"], found, lambda tr: decide.task_points(people[tr], config, model),
                                model, W, RISE, 0.1)["anticipation"]
        onset = {}
        for e in log:
            if e["kind"] == "person_state" and e["source"] == "need":
                fe = e["data"]["focused_engagement"]
                if fe["state"] != "none" and fe.get("onset_ts") is not None:
                    onset.setdefault(round(e["t"], 3), fe["onset_ts"])
        light = [(t, score.task_light(model, s, ["desk-1"]), c) for t, s, c in arms["candidate"]]
        mine = []
        for track, t0, t1 in found:
            # the onset_ts carried by the episode's first non-none state
            first = next(e for e in log if e["kind"] == "person_state" and e["source"] == "need" and e["t"] == t0)
            o = first["data"]["focused_engagement"]["onset_ts"]
            before = [x for x in light if x[0] < o][-1][1]
            look = [t for t, v, c in light if c == "need" and t >= t0 and t <= t1]
            hit_tool = any(t0 <= t <= t0 + W and c == "need" and v >= (1 + RISE) * before for t, v, c in light)
            hit_onset = any(o <= t <= o + W and c == "need" and v >= (1 + RISE) * before for t, v, c in light)
            mine.append(hit_onset)
            flips_total += hit_tool != hit_onset
            print("RESULT delay=%.0fs episode t0-onset_ts=%.2fs first need change at t0%+.2fs (onset%+.2fs) "
                  "hit(from t0)=%s hit(from onset_ts)=%s"
                  % (delay, t0 - o, (look[0] - t0) if look else float("nan"), (look[0] - o) if look else float("nan"),
                     hit_tool, hit_onset))
        print("RESULT delay=%.0fs tool anticipation=%s (scored %d); from onset_ts=%s"
              % (delay, tool["rate"], tool["scored"], round(sum(mine) / len(mine), 4) if mine else None))
    if flips_total:
        print("FAIL: %d episode(s) count as anticipated only because the window starts at need's emission time"
              % flips_total)
        return 1
    print("PASS: no episode depends on where the window starts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
