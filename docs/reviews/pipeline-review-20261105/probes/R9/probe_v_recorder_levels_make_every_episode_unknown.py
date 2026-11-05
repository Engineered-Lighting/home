#!/usr/bin/env python3
"""Probe: on the documented Home Assistant export path, the recorder gives the bulbs' on/off but no brightness or
kelvin (sources.recorder_lights writes brightness and kelvin null). den_lighting seeds its normal scene from those
observations, decide's base scene is den_lighting's normal, so the candidate's predicted task light before each
onset is unknown: score.surrogates() drops every episode as 'unknown', the rates are None, and the anticipation and
stability checks can only fail ("candidate None against floor ... (0 of N episode(s) scored)").

    python3 -B probe_v_recorder_levels_make_every_episode_unknown.py <den-core-live checkout root>

It runs a synthetic evening (need, incumbent, decide) twice through runner.run as a real-file replay (synthetic flag
off, plant off): once with the evening's light_observed rows as they are, once with their brightness and kelvin set
to null as recorder_lights writes them, and scores both with score.surrogates on the proposed parameters.
Synthetic data only; nothing written; no host contacted. Prints RESULT lines; FAIL when the recorder-shaped run
scores no episode.
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

    items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True)
    out = {}
    for label, strip in (("levels known", False), ("recorder-shaped (brightness/kelvin null)", True)):
        rows = []
        for t, kind, data in items:
            if kind == "light_observed" and strip:
                data = dict(data, brightness=None, kelvin=None)
            rows.append((t, kind, data))
        header, log = runner.run(runner.tagged(rows, "synthetic"), runner.load_policies(["need", "incumbent", "decide"]),
                                 synthetic=False, plant=False, sources=[{"name": "synthetic:evening"}])
        config = next(p["config"] for p in header["policies"] if p["id"] == "decide")
        model = light_model.LightModel(config["light_model"])
        people = {e["data"]["track_id"]: e["data"] for e in log if e["kind"] == "person_state"}
        arms, found = score.arms(header, log), score.episodes(log)
        r = score.surrogates(arms["candidate"], found, lambda tr: decide.task_points(people[tr], config, model),
                             model, 10, 0.2, 0.1)
        out[label] = r
        print("RESULT %-42s episodes=%d anticipation=%s stability=%s scored=%d unknown=%d"
              % (label, len(found), r["anticipation"]["rate"], r["stability"]["rate"], r["anticipation"]["scored"],
                 r["anticipation"]["unknown"]))
    rec = out["recorder-shaped (brightness/kelvin null)"]
    if rec["anticipation"]["scored"] == 0 and rec["anticipation"]["episodes"] > 0:
        print("FAIL: with recorder-shaped bulb states every episode is unknown; the surrogate checks cannot pass")
        return 1
    print("PASS: episodes remain scorable with recorder-shaped bulb states")
    return 0


if __name__ == "__main__":
    sys.exit(main())
