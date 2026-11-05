#!/usr/bin/env python3
"""Probe: can den-core's light model adapter (tools/den-core/light_model.py) read the light-model.json that rung 8
Stage 1 (tools/den-light, claude/rung-8-stage1) actually writes, and does it predict what den-light's own predict()
predicts?

    python3 -B probe_v_stage1_light_model_rejected.py <den-core-live checkout root> [<rung-8-stage1 checkout root>]

The second root defaults to the sibling worktree 'rung-8-stage1' next to the first. It copies rung 8's
tools/den-light to a temp folder (the pinned worktree is never written), builds a light-model.json exactly as
den-light's own LightModelTests.setUpClass does (synthetic den, synthetic readings, model.fit, model.light_model),
then:
  1. hands it to den-core's light_model.LightModel (what decide does with run.py --light-model FILE);
  2. runs den-core's run.py --light-model FILE on a tiny synthetic evening into a temp folder.
PASS when den-core accepts the file and its predictions equal den-light's predict(); FAIL otherwise.
Synthetic data only; temp folders only; no host contacted (den-light's fakes are not started).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile


def main():
    root = os.path.abspath(sys.argv[1])
    r8 = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(os.path.dirname(root), "rung-8-stage1")
    sys.dont_write_bytecode = True
    tmp = tempfile.mkdtemp(prefix="r9v-lm-")
    try:
        light = os.path.join(tmp, "den-light")
        shutil.copytree(os.path.join(r8, "tools", "den-light"), light, ignore=shutil.ignore_patterns("__pycache__"))
        sys.path.insert(0, light)
        import guard
        import model
        import predict
        import synthetic
        ranges = synthetic.KELVIN_RANGE
        warmest = {e: lo for e, (lo, _) in ranges.items()}
        snapshot = {"light.den_front_left": guard.scene(True, 180, 2700),
                    "light.front_right": guard.scene(False, 120, 3000),
                    "light.den_rear_left": guard.scene(True, 90, 2500),
                    "light.den_rear_right": guard.scene(True, 200, 4000)}
        fast = dict(block_px=40, reduce_pixel_step=10, vacancy_s=0.2, poll_s=0.02, start_wait_limit_s=10,
                    settle_limit_s=2, frames_per_capture=2, resume_check_s=3, off_check_s=0.3,
                    walkthrough_limit_s=3, unavailable_wait_s=1.0, ha_timeout_s=5, frame_timeout_s=3,
                    hold_check_wait_s=0.05)
        m = model.load_stage1().proposed().closed(**fast)
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
        path = os.path.join(tmp, "light-model.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(lm, f)
        print("RESULT stage1 light-model.json: version=%s points=%d top-level keys=%s"
              % (lm["version"], len(lm["sample_points"]), sorted(lm)))
        print("RESULT stage1 basis keys look like %s; dimming keys %s" % (sorted(lm["basis"])[:2], sorted(lm["dimming"])))
        ha = {"light.den_front_left": {"on": True, "brightness": 200, "kelvin": 2700},
              "light.front_right": {"on": False, "brightness": None, "kelvin": None},
              "light.den_rear_left": {"on": True, "brightness": 90, "kelvin": 3000},
              "light.den_rear_right": {"on": True, "brightness": 254, "kelvin": 4000}}
        theirs = predict.predict(predict.scene_from_ha(ha, lm), lm)["surfaces"]
        sys.path.remove(light)
        for name in ("guard", "model", "predict", "synthetic", "meter"):
            sys.modules.pop(name, None)
        sys.path.insert(0, os.path.join(root, "tools", "den-core"))
        import light_model
        ok = True
        try:
            mine = light_model.LightModel(lm)
            ours = mine.predict(ha)
            diff = max(abs(ours[p] - theirs[p]) for p in ours if ours[p] is not None and theirs.get(p) is not None)
            print("RESULT den-core accepted it; fake=%s; max |den-core - den-light| = %g" % (mine.fake, diff))
            ok = diff < 1e-9
        except Exception as error:  # noqa: BLE001 - report whatever the adapter raises
            ok = False
            print("FAIL den-core light_model.LightModel(stage-1 light-model.json) raised %s: %s"
                  % (type(error).__name__, error))
        out = os.path.join(tmp, "run")
        proc = subprocess.run([sys.executable, "-B", os.path.join(root, "tools", "den-core", "run.py"), "--out", out,
                               "--synthetic", "evening", "--policies", "need", "incumbent", "decide",
                               "--light-model", path], capture_output=True, text=True, cwd=tmp)
        tail = (proc.stderr.strip().splitlines() or [""])[-1]
        print("%s run.py --light-model <stage-1 file>: exit %d %s" % ("PASS" if proc.returncode == 0 else "FAIL",
                                                                      proc.returncode, tail[:300]))
        ok = ok and proc.returncode == 0
        print("PASS" if ok else "FAIL: den-core cannot use rung 8 Stage 1's light-model.json")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
