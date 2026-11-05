"""probe_atk_task_points_from_future: score.py scores every need episode of a track at the task points of that
track's LAST PersonState in the whole log, not the one it had during the episode.

What it checks
  score.score builds people = {track_id: data for every person_state in the log} (the last one wins) and
  points_of(track) = decide.task_points(people[track], ...), so a track whose working_region changes during the
  evening has all its episodes scored where it ended up. decide itself plans causally on the latest PersonState.

  The log is made by the real runner with the real incumbent (den_lighting policy.py) and decide; need is
  replaced by a scripted stand-in policy (id 'need') that emits PersonStates for track A:
    episodes 1 and 2: A reads in the armchair (working_region armchair-1),
    episode 3:        A reads at the desk (desk-1) -- the evening's last PersonState says desk-1.
  B dimmed the two rear bulbs earlier (two corrections), so den_lighting holds them and decide pins them: the
  reading look can only go on the two front bulbs, which light the desk and not the armchair (light_model.fake).
  Correct (each episode at its own region): armchair, armchair, desk -> anticipation 1/3.
  score.py: all three at desk-1 -> anticipation 1.0. With the gate's proposed floor 0.5 that flips fail -> pass.

How to run (synthetic data only; temp folder only; no host contacted):
  python3 -B probe_atk_task_points_from_future.py /path/to/den-core-live-checkout
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / "tools" / "den-core"))
    import decide
    import events
    import light_model
    import runner
    import score

    T0 = 1793764800.0
    FL, FR, RL, RR = events.LIGHTS
    LIT = {"on": True, "brightness": 180, "kelvin": 2700}

    script = []  # (t, state, region)
    for n, region in enumerate(("armchair-1", "armchair-1", "desk-1")):
        base = 100 + 300 * n
        script += [(base, "starting", region), (base + 6, "engaged", region), (base + 200, "none", region)]

    class ScriptedNeed:
        id, version, code_path = "need", "scripted-probe", __file__
        config = {"script": script}

        def __init__(self):
            self.next = 0

        def handle(self, event):
            out = []
            while self.next < len(script) and event["t"] >= T0 + script[self.next][0]:
                t, state, region = script[self.next]
                self.next += 1
                active = state != "none"
                out.append({"kind": "person_state", "data": {
                    "schema_version": "person_state/v1", "state_id": "A@%.3f" % event["t"], "emitted_ts": event["t"],
                    "track_id": "A", "identity": "unbound", "working_region": {"surface_id": region},
                    "focused_engagement": {"state": state, "since_s": 0.0 if active else None,
                                           "onset_ts": event["t"] if active else None, "confidence": None,
                                           "kind": "reading"},
                    "field_support": {"focused_engagement": {"last_support_capture_ts": event["t"] - 1.0,
                                                             "value_set_ts": event["t"], "support_status": "observed"}},
                    "flags": []}})
            return out

    inputs = [(T0, "light_observed", {"entity_id": e, **LIT, "context": "unknown"}, "recorder_lights")
              for e in events.LIGHTS]
    inputs += [(T0, "occupancy", {"value": "on", "via": "den_lighting_status"}, "den_lighting_status")]
    inputs += [(T0 + 5, "correction", {"correction_id": "c1", "entity_id": b,
                                       "after": {"on": True, "brightness": 60, "kelvin": 2700}, "source": "probe"},
                "correction_capture") for b in (RL, RR)]
    inputs += [(T0 + 1000, "occupancy", {"value": "on", "via": "den_lighting_status"}, "den_lighting_status")]

    slots = {n: {**e, "value": e["proposed"], "status": "set"} for n, e in decide.DEFAULT_CONFIG["slots"].items()}
    model = light_model.fake()
    model.update(version="probe-lm-1", fake=False)
    policies = [ScriptedNeed()] + runner.load_policies(["incumbent", "decide"],
                                                        {"decide": {"slots": slots, "light_model": model}})
    header, log = runner.run(inputs, policies, plant=True)
    tmp = Path(tempfile.mkdtemp(prefix="probe-r9-future-"))
    try:
        events.write(tmp / "log.jsonl", header, log)
        decided = [e for e in log if e["kind"] == "decision" and e["source"] == "decide"]
        for e in decided:
            d = e["data"]
            print("INFO decide %+7.1f s %-13s held=%s scene=%s" % (
                e["t"] - T0, d["why"].split(":")[0], sorted(h["entity_id"] for h in d["inputs"]["hold_set"]["held"]),
                {b.split(".")[1]: s["brightness"] if s["on"] else "off" for b, s in d["requested_scene"].items()}))
        found = score.episodes(log)
        timeline = score.arms(header, log)
        lm = light_model.LightModel(model)
        config = next(p["config"] for p in header["policies"] if p["id"] == "decide")
        people_last = {e["data"]["track_id"]: e["data"] for e in log if e["kind"] == "person_state"}

        def tool_points(track):
            return decide.task_points(people_last[track], config, lm)

        tool = score.surrogates(timeline["candidate"], found, tool_points, lm, 10, 0.2, 0.1)

        # Causal: each episode at the region its own onset PersonState gave.
        per_episode = []
        for track, t0, t1 in found:
            onset_state = next(e["data"] for e in log if e["kind"] == "person_state" and e["t"] == t0
                               and e["data"]["track_id"] == track)
            pts = decide.task_points(onset_state, config, lm)
            one = score.surrogates(timeline["candidate"], [(track, t0, t1)], lambda _t, pts=pts: pts, lm, 10, 0.2, 0.1)
            per_episode.append((round(t0 - T0, 1), onset_state["working_region"]["surface_id"],
                                one["anticipation"]["rate"]))
        causal = [r for _, _, r in per_episode if r is not None]
        causal_rate = round(sum(causal) / len(causal), 4) if causal else None
        print("INFO episodes (onset s, region at onset, anticipation scored at that region): %s" % per_episode)
        print("INFO the last PersonState of track A says %s; score.py scores every episode there: %s"
              % (people_last["A"]["working_region"]["surface_id"], tool_points("A")))
        print("RESULT anticipation: score.py %s, causal per-episode %s; proposed floor 0.5 -> score.py %s, causal %s"
              % (tool["anticipation"]["rate"], causal_rate,
                 "pass" if (tool["anticipation"]["rate"] or 0) >= 0.5 else "fail",
                 "pass" if (causal_rate or 0) >= 0.5 else "fail"))
        # And through score.score itself (plumbing). The scripted need cannot be re-imported by check.check, so the
        # replay step is stubbed out here; the surrogate code path (people, points_of, arms, episodes) is score's own.
        score.replay_timed = lambda _path: ({"ok": False, "reason": "probe: scripted need is not replayable"}, [], 0.0)
        record, _ = score.score(tmp / "log.jsonl", plumbing=True)
        print("RESULT score.score (plumbing) candidate anticipation: %s"
              % record["surrogates"]["arms"]["candidate"]["anticipation"]["rate"])
        if tool["anticipation"]["rate"] != causal_rate:
            print("FAIL score.py reads each track's future working region: %s vs %s"
                  % (tool["anticipation"]["rate"], causal_rate))
        else:
            print("PASS no difference")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
