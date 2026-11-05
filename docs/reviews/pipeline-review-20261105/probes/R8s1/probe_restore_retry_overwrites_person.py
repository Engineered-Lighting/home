#!/usr/bin/env python3
"""Does probe.py's restore retry write over a bulb a person changed during the hold-check wait?

    python3 -I probe_restore_retry_overwrites_person.py <branch-checkout-root> [pause]

Runs one full probe session against the tool's own fakes (synthetic.FakeHA running den_lighting
1.0.4, synthetic.FakeCamera), both bound to 127.0.0.1, in a temp folder. Default mode: during the
restore's first hold-check wait (after the tool's restore writes, den_lighting still paused), a
person changes light.den_rear_left with a physical switch (60 / 2700 K, no tool context). The
ladder (rung 7/8 probe rule, A12) says no remaining flash or restore overwrites a light a person
changed. PASS if the tool never writes that bulb again after the person's change; FAIL if the
retry (attempt 2) writes the snapshot over it.

Mode `pause`: the person's change lands right after the tool pauses den_lighting (between the
vacancy read and the post-pause read). PASS if the session aborts and never writes that bulb
afterwards; FAIL if the first probe step overwrites it with no abort.

Nothing here contacts another host. Synthetic data only.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
MODE = sys.argv[2] if len(sys.argv) > 2 else "restore"
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import guard  # noqa: E402
import meter  # noqa: E402
import model  # noqa: E402
import probe  # noqa: E402
import sweep  # noqa: E402
import synthetic  # noqa: E402

HA_TOKEN, CAM_TOKEN = "h" * 32, "c" * 32
SNAPSHOT = {"light.den_front_left": guard.scene(True, 180, 2700),
            "light.front_right": guard.scene(False, 120, 3000),
            "light.den_rear_left": guard.scene(True, 90, 2500),
            "light.den_rear_right": guard.scene(True, 200, 4000)}
PERSON_BULB = "light.den_rear_left"
WAIT = 0.0517  # a unique hold_check_wait_s so the sleep hook can find the hold-check wait


def store():
    return {"version": 1, "minor_version": 1, "key": "den_lighting.state",
            "data": {"version": 1, "enabled": True, "gaming_enabled": False, "gaming": False,
                     "normal": SNAPSHOT, "snapshot": {}, "holds": {}}}


def main():
    m1 = model.load_stage1().proposed().closed(
        block_px=40, reduce_pixel_step=10, vacancy_s=0.2, poll_s=0.02, start_wait_limit_s=10, settle_limit_s=2,
        frames_per_capture=2, resume_check_s=3, off_check_s=0.3, walkthrough_limit_s=3, unavailable_wait_s=1.0,
        ha_timeout_s=5, frame_timeout_s=3, hold_check_wait_s=WAIT)
    base0 = meter.load_method()
    m0 = meter.Method({**base0.values, "pixel_step": 10, "poll_s": 0.005}, (), base0.file_sha256, base0.block_sha256)
    ha = synthetic.FakeHA(token=HA_TOKEN, store=store(), speed=600)
    cam = synthetic.FakeCamera(synthetic.SyntheticDen(block_px=40), ha, token=CAM_TOKEN, step=10)
    marks = {}

    def person():
        marks["t"] = ha.now()
        ha.set_light(PERSON_BULB, on=True, brightness=60, kelvin=2700)  # physical switch: no tool context
        marks["normal_after_person"] = dict(ha.store()["data"]["normal"][PERSON_BULB])

    def sleep(seconds):
        if MODE == "restore" and seconds == WAIT and "t" not in marks:
            time.sleep(0.02)  # past den_lighting's 3 HA-second window for the tool's own write (speed 600)
            person()
        time.sleep(seconds)

    real_call = probe.HA.call

    def call(self_, domain, service, data):
        out = real_call(self_, domain, service, data)
        if MODE == "pause" and (domain, service) == ("switch", "turn_off") and "t" not in marks:
            person()
        return out

    probe.HA.call = call
    try:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "s1"
            folder.mkdir()
            s = probe.Session(folder, probe.HA(ha.url, HA_TOKEN, 5), sweep.Camera(cam.url, CAM_TOKEN), m1, m0,
                              sleep=sleep, say=lambda *_: None)
            outcome, lines = s.run(json.dumps(store()))
            record = json.loads((folder / "session.json").read_text())
            log = probe.read_log(folder / "probes.jsonl")
    finally:
        probe.HA.call = real_call
        ha.close()
        cam.close()
    if "t" not in marks:
        print("RESULT the person's change was never injected (hook not reached)")
        return 2
    after = [c for c in ha.calls if c[5] == "rest" and c[0] >= marks["t"] and c[1] == "light"
             and c[3].get("entity_id") == PERSON_BULB]
    normal = ha.store()["data"]["normal"][PERSON_BULB]
    print(f"RESULT mode={MODE} outcome={outcome} abort_reason={record.get('abort_reason')!r}")
    print(f"RESULT den_lighting normal for {PERSON_BULB} right after the person's change: "
          f"{ {k: marks['normal_after_person'][k] for k in ('on', 'brightness', 'kelvin')} }")
    print(f"RESULT tool light writes to {PERSON_BULB} after the person's change: "
          f"{[(c[2], c[3].get('brightness'), c[3].get('color_temp_kelvin')) for c in after]}")
    print(f"RESULT final den_lighting normal for {PERSON_BULB}: "
          f"{ {k: normal[k] for k in ('on', 'brightness', 'kelvin')} }; unrestored={record.get('unrestored')}")
    checks = [l for l in log if l["kind"] == "hold_check"]
    print(f"RESULT hold checks: {[(c['attempt'], c['failures']) for c in checks]}")
    if after:
        print(f"FAIL the tool wrote {PERSON_BULB} {len(after)} time(s) after a person changed it "
              f"(outcome {outcome}); the person's scene is gone from den_lighting's normal")
        return 1
    print(f"PASS no tool write to {PERSON_BULB} after the person's change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
