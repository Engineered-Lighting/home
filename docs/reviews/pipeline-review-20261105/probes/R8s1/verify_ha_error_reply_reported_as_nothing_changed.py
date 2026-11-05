#!/usr/bin/env python3
"""Verify: a non-200 HA reply mid-session ends probe.py with 'This command changed nothing' while
den_lighting stays paused -- using the reply shapes a real Home Assistant sends.

    python3 -I verify_ha_error_reply_reported_as_nothing_changed.py <branch-checkout-root>

The reporter's probe makes the fake answer 400 with a JSON body. A real HA mostly answers errors
with aiohttp's plain-text bodies ("400: Bad Request", "500 Internal Server Error ..."), and while
HA is stopping (a restart) its request_handler_factory answers 503 with an EMPTY body. probe.py's
HA.request parses JSON before it checks the status, so:
  - a text body  -> HAUnreachable('reply is not JSON') -> handled as HA lost (abort, --recover)
  - an empty or JSON body with status != 200 -> sweep.Refused -> escapes Session.run
This probe runs probe.main(["--session", ...]) against the tool's own fakes on 127.0.0.1
(synthetic.FakeHA wrapped so it can answer like a stopping HA), METHOD slots closed at their
proposed values with test timings (model.load_stage1 patched), in a temp folder.

Modes (both run):
  stopping : after the 20th light call is applied, every request gets 503 with an empty body
             (HA restarting). FAIL if the CLI says it changed nothing while den_lighting is paused.
  text500  : the 20th light call gets 500 with a plain-text body. Expected: handled as HA lost.
Synthetic data only; no other host is contacted.
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


class StoppingHA(synthetic.FakeHA):
    """FakeHA that can answer like a real HA: 503 empty while stopping, 500 text on one call."""

    def __init__(self, mode, **kw):
        self.mode, self.stopping, self.light_calls = mode, False, 0
        super().__init__(**kw)

    def _handler(self):
        base, fake = super()._handler(), self

        class Handler(base):
            def raw(self, status, body, ctype):
                self.send_response(status)
                if ctype:
                    self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if fake.stopping:
                    return self.raw(503, b"", None)
                return base.do_GET(self)

            def do_POST(self):
                if fake.stopping:
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    return self.raw(503, b"", None)
                if self.path.startswith("/api/services/light/"):
                    fake.light_calls += 1
                    if fake.mode == "text500" and fake.light_calls == 20:
                        self.rfile.read(int(self.headers.get("Content-Length", "0")))
                        return self.raw(500, b"500 Internal Server Error\n\nServer got itself in trouble",
                                        "text/plain; charset=utf-8")
                    out = base.do_POST(self)
                    if fake.mode == "stopping" and fake.light_calls == 20:
                        fake.stopping = True
                    return out
                return base.do_POST(self)

        return Handler


def run(mode):
    m1 = model.load_stage1().proposed().closed(
        block_px=40, reduce_pixel_step=10, vacancy_s=0.2, poll_s=0.02, start_wait_limit_s=10, settle_limit_s=2,
        frames_per_capture=2, resume_check_s=3, off_check_s=0.3, walkthrough_limit_s=3, unavailable_wait_s=1.0,
        ha_timeout_s=5, frame_timeout_s=3, hold_check_wait_s=0.05)
    base0 = meter.load_method()
    m0 = meter.Method({**base0.values, "pixel_step": 10, "poll_s": 0.005}, (), base0.file_sha256, base0.block_sha256)
    model.load_stage1 = lambda path=None: m1
    meter.load_method = lambda path=None: m0
    ha = StoppingHA(mode, token=HA_TOKEN, store=STORE, speed=600)
    cam = synthetic.FakeCamera(synthetic.SyntheticDen(block_px=40), ha, token=CAM_TOKEN, step=10)
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
            with ha.lock:
                switch = ha.states[synthetic.SWITCH]["state"]
                lit = [e for e in guard.LIGHTS if ha.lights[e]["on"]]
            print(f"RESULT [{mode}] exit={code}")
            print(f"RESULT [{mode}] stdout={out.getvalue().strip()!r}")
            print(f"RESULT [{mode}] stderr={err.getvalue().strip()!r}")
            print(f"RESULT [{mode}] session.json phase={record['phase']!r} outcome={record['outcome']!r}")
            print(f"RESULT [{mode}] den_lighting automatic switch={switch!r}; bulbs on={lit}; camera held={cam.held}")
            return code, err.getvalue(), out.getvalue(), switch, record
    finally:
        ha.close()
        cam.close()


def main():
    fails = 0
    code, err, out, switch, record = run("stopping")
    if code == 2 and "changed nothing" in err and switch == "off":
        print("FAIL [stopping] HA restarting (503, empty body, as HA answers while stopping) makes probe.py say "
              "'This command changed nothing' (exit 2) while den_lighting is left paused and no --recover is named")
        fails += 1
    else:
        print("PASS [stopping] handled")
    code, err, out, switch, record = run("text500")
    if code == 1 and "--recover" in out and record["phase"] == "ha_lost":
        print("NOTE [text500] a plain-text 500 is handled as HA lost (abort, paused, tells the owner to --recover)")
    elif code == 2 and "changed nothing" in err:
        print("FAIL [text500] a plain-text 500 also escapes as 'changed nothing'")
        fails += 1
    else:
        print(f"NOTE [text500] other handling: exit {code}")
    print("RESULT", "PASS" if not fails else f"{fails} FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
