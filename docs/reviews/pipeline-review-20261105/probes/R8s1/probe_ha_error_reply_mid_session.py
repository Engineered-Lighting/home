#!/usr/bin/env python3
"""An HA error reply (non-200) to one probe write, mid-session, through probe.py's CLI.

    python3 -I probe_ha_error_reply_mid_session.py <branch-checkout-root>

Runs probe.main(["--session", ...]) against the tool's own fakes on 127.0.0.1 (synthetic.FakeHA
with den_lighting 1.0.4, synthetic.FakeCamera), with token files, a store copy and a passing
Stage 0 response.json in a temp folder. METHOD's Stage 1 slots are closed at their proposed
values (model.load_stage1 patched; the branch's METHOD still has them open, so the real CLI
refuses before any of this) and test timings are used. The fake HA answers its 20th light
service call with an error reply (the fake's HAError -> HTTP 400; a real HA answers 500 when an
integration raises during a service call, e.g. the Hue bridge briefly unreachable). FAIL if the
CLI then says it changed nothing (exit 2) while den_lighting is left paused, bulbs are left lit,
and no restore or resume happened. Synthetic data only; no other host is contacted.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import guard  # noqa: E402
import meter  # noqa: E402
import model  # noqa: E402
import probe  # noqa: E402
import synthetic  # noqa: E402

HA_TOKEN, CAM_TOKEN = "h" * 32, "c" * 32
SNAPSHOT = {"light.den_front_left": guard.scene(True, 180, 2700), "light.front_right": guard.scene(False, 120, 3000),
            "light.den_rear_left": guard.scene(True, 90, 2500), "light.den_rear_right": guard.scene(True, 200, 4000)}
STORE = {"version": 1, "minor_version": 1, "key": "den_lighting.state",
         "data": {"version": 1, "enabled": True, "gaming_enabled": False, "gaming": False, "normal": SNAPSHOT,
                  "snapshot": {}, "holds": {}}}


def main():
    m1 = model.load_stage1().proposed().closed(
        block_px=40, reduce_pixel_step=10, vacancy_s=0.2, poll_s=0.02, start_wait_limit_s=10, settle_limit_s=2,
        frames_per_capture=2, resume_check_s=3, off_check_s=0.3, walkthrough_limit_s=3, unavailable_wait_s=1.0,
        ha_timeout_s=5, frame_timeout_s=3, hold_check_wait_s=0.05)
    base0 = meter.load_method()
    m0 = meter.Method({**base0.values, "pixel_step": 10, "poll_s": 0.005}, (), base0.file_sha256, base0.block_sha256)
    model.load_stage1 = lambda path=None: m1
    meter.load_method = lambda path=None: m0
    ha = synthetic.FakeHA(token=HA_TOKEN, store=STORE, speed=600)
    cam = synthetic.FakeCamera(synthetic.SyntheticDen(block_px=40), ha, token=CAM_TOKEN, step=10)
    real_call, count = ha.call, {"n": 0}

    def call(domain, service, data):
        if domain == "light":
            count["n"] += 1
            if count["n"] == 20:
                raise synthetic.HAError("Failed to call service light/turn_on")  # fake replies 400
        return real_call(domain, service, data)

    ha.call = call
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "private").mkdir()
            (tmp / "private" / "ha-token.txt").write_text(HA_TOKEN)
            (tmp / "private" / "camera-token.txt").write_text(CAM_TOKEN)
            (tmp / "private" / "den_lighting.state").write_text(json.dumps(STORE))
            stage0 = tmp / "stage0" / "sweep-1"
            stage0.mkdir(parents=True)
            params, digest = synthetic.meter_norm_for(m0)
            (stage0 / "response.json").write_text(json.dumps(
                {"outcome": "pass", "meter_norm": {"parameters": params, "parameters_sha256": digest}}))
            session = tmp / "s1-probe"
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = probe.main(["--session", str(session), "--ha", ha.url, "--camera", cam.url,
                                   "--stage0", str(stage0), "--store-copy", str(tmp / "private" / "den_lighting.state"),
                                   "--ha-token-file", str(tmp / "private" / "ha-token.txt"),
                                   "--camera-token-file", str(tmp / "private" / "camera-token.txt")])
            record = json.loads((session / "session.json").read_text())
            switch = ha.states[synthetic.SWITCH]["state"]
            lit = [e for e in guard.LIGHTS if ha.lights[e]["on"]]
            print(f"RESULT exit={code} stdout={out.getvalue().strip()!r}")
            print(f"RESULT stderr={err.getvalue().strip()!r}")
            print(f"RESULT session.json phase={record['phase']!r} outcome={record['outcome']!r} "
                  f"restore={record['restore']!r} resume={record['resume']!r}")
            print(f"RESULT den_lighting automatic switch={switch!r}; bulbs left on={lit}; "
                  f"camera still held={cam.held}")
            fails = 0
            if code == 2 and "changed nothing" in err.getvalue() and switch == "off":
                print("FAIL the CLI says 'This command changed nothing' (exit 2, README: nothing was changed) while "
                      "den_lighting is left paused and the session never restored or resumed")
                fails += 1
            if lit and record["restore"] is None:
                print(f"FAIL bulbs {lit} left at a probe level with no restore")
                fails += 1
            print("RESULT", "PASS" if not fails else f"{fails} FAIL")
            return 1 if fails else 0
    finally:
        ha.close()
        cam.close()


if __name__ == "__main__":
    sys.exit(main())
