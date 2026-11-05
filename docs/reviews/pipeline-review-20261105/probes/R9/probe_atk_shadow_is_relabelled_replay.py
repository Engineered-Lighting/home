"""probe_atk_shadow_is_relabelled_replay: is a den-core "shadow run" anything but an offline replay with a label?

What it checks
  run.py --actuation shadow (runner.run actuation='shadow') needs the plant off and real-file inputs, then runs the
  same deterministic replay of copied files. This probe runs the same inputs (four recorded bulbs, den_lighting's
  occupancy on and off, one hold) twice through runner.run with plant off -- once actuation='replay', once
  'shadow' -- and compares every logged event with the 'actuation' key removed, then asks score.refusals whether a
  shadow-labelled log is accepted as a scored run.

How to run (synthetic data only; nothing written; no host contacted):
  python3 -B probe_atk_shadow_is_relabelled_replay.py /path/to/den-core-live-checkout
"""
import json
import sys
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
    LIT = {"on": True, "brightness": 180, "kelvin": 2700}
    inputs = [(T0, "light_observed", {"entity_id": e, **LIT, "context": "unknown"}, "recorder_lights")
              for e in events.LIGHTS]
    inputs += [(T0, "occupancy", {"value": "on", "via": "den_lighting_status"}, "den_lighting_status"),
               (T0 + 20, "correction", {"correction_id": "c1", "entity_id": events.LIGHTS[0],
                                        "after": {"on": True, "brightness": 60, "kelvin": 2700}, "source": "probe"},
                "correction_capture"),
               (T0 + 60, "occupancy", {"value": "off", "via": "den_lighting_status"}, "den_lighting_status")]
    slots = {n: {**e, "value": e["proposed"], "status": "set"} for n, e in decide.DEFAULT_CONFIG["slots"].items()}
    model = light_model.fake()
    model.update(version="probe-lm-1", fake=False)
    configs = {"decide": {"slots": slots, "light_model": model}}
    logs = {}
    for actuation in ("replay", "shadow"):
        header, log = runner.run(inputs, runner.load_policies(["incumbent", "decide"], configs), plant=False,
                                 actuation=actuation)
        stripped = []
        for e in log:
            data = {k: v for k, v in e["data"].items() if k != "actuation"}
            stripped.append(events.dumps({**e, "data": data}))
        logs[actuation] = (header, log, stripped)
    same = logs["replay"][2] == logs["shadow"][2]
    n = sum(1 for e in logs["shadow"][1] if e["kind"] == "decision")
    print("INFO %d decision(s) in each run; %d events" % (n, len(logs["shadow"][1])))
    print("%s replay and shadow logs differ in more than the actuation label" % ("FAIL" if same else "PASS"))
    gate = json.loads(score.GATE.read_text("utf-8"))
    for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
        entry.update(value=entry["proposed"], status="set")
    gate["parameters"]["compensation"]["value"] = "none-v0"
    header = logs["shadow"][0]
    config = next(p["config"] for p in header["policies"] if p["id"] == "decide")
    reasons = score.refusals(header, gate, config, light_model.LightModel(config["light_model"]))
    print("INFO score.refusals for the shadow-labelled log: %s" % (reasons or "none (accepted as a scored run)"))
    if same and not reasons:
        print("RESULT DEFECT REPRODUCED: a 'shadow' run is the offline replay with another label, and is scored as one")


if __name__ == "__main__":
    main()
