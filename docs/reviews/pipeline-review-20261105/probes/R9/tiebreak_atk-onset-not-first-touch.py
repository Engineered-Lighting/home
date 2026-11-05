#!/usr/bin/env python3
"""tiebreak_atk-onset-not-first-touch: what does score.episodes() use as the anticipation onset?

What it checks
  Owner choice 3 (contact-starting): the gold onset is the first touch, and the system state that is timed
  against it is the system's first output that leaves 'none' ('starting'). PersonState.emitted_ts is
  "when the record left K7" (schemas.json). The probe runs a synthetic evening (need, incumbent, decide; synthetic
  flag on, so plumbing only) and, for every episode score.episodes() returns, checks:
    1. t0 equals the emitted_ts of need's first PersonState that leaves none in that episode, and that state is
       'starting' (i.e. t0 is the timed system state of the contact-starting protocol);
    2. how far t0 lies after need's own focused_engagement.onset_ts (the capture time of the first playing
       sample), i.e. the pipeline latency that the window excludes;
    3. whether score.score() takes any gold or first-touch input (it does not: its signature is listed).
  It prints RESULT lines. It says PASS when t0 is the starting output's emitted_ts for every episode (the tool
  times the protocol's system state, as its gate text says), FAIL otherwise.

How to run (synthetic data only, no files written, no host contacted):
  python3 -B tiebreak_atk-onset-not-first-touch.py /path/to/den-core-live-checkout
"""
import inspect
import os
import sys


def main():
    root = os.path.abspath(sys.argv[1])
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.join(root, "tools", "den-core"))
    import runner
    import score
    import synthetic

    items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True, game=True)
    header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need", "incumbent", "decide"]),
                             synthetic=True, sources=[{"name": "synthetic:evening"}])
    found = score.episodes(log)
    ok = True
    for track, t0, t1 in found:
        states = [e for e in log if e["kind"] == "person_state" and e["source"] == "need"
                  and e["data"]["track_id"] == track and t0 <= e["t"] <= t1]
        first = states[0]["data"]
        fe = first["focused_engagement"]
        same = abs(first["emitted_ts"] - t0) < 1e-9 and fe["state"] == "starting"
        ok &= same
        print("RESULT episode %s t0=%.3f first state=%s emitted_ts=%.3f (t0==emitted_ts and starting: %s); "
              "onset_ts=%.3f, t0-onset_ts=%.2fs" % (track, t0, fe["state"], first["emitted_ts"], same,
                                                     fe["onset_ts"], t0 - fe["onset_ts"]))
    print("RESULT score.score signature: %s" % (inspect.signature(score.score),))
    print("RESULT episodes: %d" % len(found))
    if ok and found:
        print("PASS: the anticipation onset is need's first 'starting' output at its emitted_ts, the system state "
              "contact-starting times; no gold first-touch input exists in score.py")
        return 0
    print("FAIL: an episode's onset is not need's starting output")
    return 1


if __name__ == "__main__":
    sys.exit(main())
