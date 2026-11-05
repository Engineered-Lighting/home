"""probe_atk_stage1_model_refused: does den-core's light-model adapter accept the light-model.json that rung 8
Stage 1 (claude/rung-8-stage1, tools/den-light) actually writes?

What it checks
  score.py refuses a scored run on the fake model and asks for "rung 8 Stage 1's light-model.json (run.py
  --light-model)". light_model.py says Stage 1 "is not built yet" and reads its own guess of the format
  (dimming/basis keyed by HA entity id, basis[entity][kelvin] = {point: value}). Stage 1 is built on its branch.
  This probe builds a Stage 1 light-model.json with den-light's OWN code on a synthetic den (exactly as
  den-light's test_stage1.LightModelTests does: synthetic readings -> model.fit -> model.score ->
  model.light_model), writes it to a temp folder, and hands it to
    1. den-core light_model.LightModel(...)            (the adapter decide and score.py use)
    2. den-core run.py --light-model FILE ...           (the owner's documented path)
  and also checks den-light's own predict() on the same file works (so the file itself is good).

How to run (synthetic data only, temp folder only, no host contacted):
  python3 -B probe_atk_stage1_model_refused.py /path/to/den-core-live-checkout /path/to/rung-8-stage1-checkout
"""
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def build_stage1_model(r8):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(r8 / "tools" / "den-light"))
    import guard  # noqa: F401
    import model
    import predict
    import synthetic
    ranges = synthetic.KELVIN_RANGE
    warmest = {e: lo for e, (lo, _) in ranges.items()}
    snapshot = {"light.den_front_left": guard.scene(True, 180, 2700),
                "light.front_right": guard.scene(False, 120, 3000),
                "light.den_rear_left": guard.scene(True, 90, 2500),
                "light.den_rear_right": guard.scene(True, 200, 4000)}
    base = model.load_stage1().proposed()
    m = base.closed(block_px=40, reduce_pixel_step=10, vacancy_s=0.2, poll_s=0.02, start_wait_limit_s=10,
                    settle_limit_s=2, frames_per_capture=2, resume_check_s=3, off_check_s=0.3, walkthrough_limit_s=3,
                    unavailable_wait_s=1.0, ha_timeout_s=5, frame_timeout_s=3, hold_check_wait_s=0.05)
    den = synthetic.SyntheticDen(block_px=40, noise=1e-4)
    plan = model.schedule(m, snapshot, ranges, warmest)
    steps, t = [], 0.0
    for step in plan["steps"]:
        t += 2.5
        steps.append({**step, "t": t, "reading": den.reading(step["scene"], t),
                      "spread": [max(den.noise, 1e-6)] * den.blocks})
    readings = {"schema": model.SCHEMAS["readings"], "session": "synthetic", "blocks": den.blocks,
                "mask": {"masked_pixels": 0, "pixels": den.blocks * 16, "unmasked_share": [1.0] * den.blocks},
                "steps": steps, "temperatures": plan["temperatures"]}
    fitted = model.fit(readings, readings["temperatures"], m)
    scored = model.score(readings, fitted, m)
    fixtures = [{"record": "fixture", "contract": "live-v1", "fixture_id": e.split(".", 1)[1], "model": "hue-den-4",
                 "pose": {"position": None, "method": "assumed"},
                 "channels": [{"channel_id": "white", "kind": "tunable_white", "entity_id": e,
                               "cct_range_k": list(ranges[e])}],
                 "orientation_calibration": None, "optics": None, "dimming_curve": None, "gimbal": None}
                for e in guard.LIGHTS]
    lm = json.loads(json.dumps(model.light_model(fitted, fixtures, m, session_id="s1", readings_sha256="0" * 64,
                                                 meter_norm_sha256="1" * 64, score_error=scored["error"])))
    scene = {"light.den_front_left": guard.scene(True, 100, 3100), "light.front_right": guard.scene(False),
             "light.den_rear_left": guard.scene(True, 254, 5000), "light.den_rear_right": guard.scene(True, 30, 2500)}
    out = predict.predict(predict.scene_from_ha(scene, lm), lm)
    return lm, out


def main():
    core = Path(sys.argv[1]).resolve()
    r8 = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else core.parent / "rung-8-stage1"
    lm, own = build_stage1_model(r8)
    points = [k for k in own["surfaces"] if k != "unit"]
    print("INFO den-light wrote light-model.json version %s, %d sample points, keys: %s"
          % (lm["version"], len(points), ", ".join(sorted(lm))))
    print("PASS den-light's own predict() reads it: %d surfaces, unit %s" % (len(points), own["surfaces"]["unit"]))

    tmp = Path(tempfile.mkdtemp(prefix="probe-r9-stage1-"))
    try:
        path = tmp / "light-model.json"
        path.write_text(json.dumps(lm, indent=1), "utf-8")
        sys.path.insert(0, str(core / "tools" / "den-core"))
        import light_model
        try:
            light_model.LightModel(lm)
            print("PASS den-core LightModel accepts the Stage 1 model")
            adapter_ok = True
        except ValueError as error:
            print("FAIL den-core LightModel refuses the Stage 1 model: %s" % error)
            adapter_ok = False
        status = tmp / "status.jsonl"
        status.write_text(json.dumps({"last_updated_ts": 1793764800.0, "entity_id": "sensor.den_lighting_control",
                                      "state": "occupancy", "attributes": {"automatic": True, "gaming_session": False,
                                                                           "occupancy": "on", "revision": 3}}) + "\n",
                          "ascii")
        cmd = [sys.executable, "-B", str(core / "tools" / "den-core" / "run.py"), "--status", str(status), "--no-plant",
               "--light-model", str(path), "--policies", "incumbent", "decide", "--out", str(tmp / "out")]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
        print("RESULT run.py --light-model <Stage 1 file>: exit %d, last line: %s" % (proc.returncode, tail[0][:300]))
        print("RESULT %s" % ("DEFECT REPRODUCED: rung 8 Stage 1's own light-model.json cannot be used by den-core, so "
                             "score.py's only non-fake model path is closed" if not adapter_ok or proc.returncode
                             else "no defect: the Stage 1 model is accepted"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
