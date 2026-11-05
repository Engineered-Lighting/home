#!/usr/bin/env python3
"""Verifier probe for finding ha-error-reply-reported-as-nothing-changed.

    python3 -B -I probe_verify_ha_error_reply_shapes.py <root of the claude/rung-8-stage1 checkout>

Part 1 (unit): a tiny HTTP server on 127.0.0.1 answers probe.HA.request() with the reply
shapes a real Home Assistant sends, and the probe prints which exception the tool raises:
  - aiohttp's default 500 body for an exception raised in a service handler (text/plain),
  - aiohttp's HTTPBadRequest body ("400: Bad Request"), as HA raises for vol.Invalid / ServiceNotFound,
  - aiohttp's HTTPUnauthorized body ("401: Unauthorized"), as HA raises for a bad token,
  - HA's reply while it is stopping: 503 with an EMPTY body (helpers/http.py, hass.is_stopping),
  - the tool's own fake: 400 with a JSON body {"message": ...} (what the reporter's probe used).
HAUnreachable is what Session.send/read/ha_call turn into an ha_lost Abort (the right path);
sweep.Refused escapes them and reaches main()'s "This command changed nothing".

Part 2 (end to end): probe.main(["--session", ...]) against the tool's own FakeHA/FakeCamera on
127.0.0.1, with the 20th light service call answered 503 with an empty body (HA stopping during
a restart). Prints the exit code, stderr, session.json phase and the fake den_lighting's state.
Synthetic data only, temp dirs only, no other host.
"""
import contextlib
import io
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import guard  # noqa: E402
import meter  # noqa: E402
import model  # noqa: E402
import probe  # noqa: E402
import sweep  # noqa: E402
import synthetic  # noqa: E402

SHAPES = [
    ("500 text (aiohttp default, service raised)", 500, b"500 Internal Server Error\n\nServer got itself in trouble",
     "text/plain"),
    ("400 text (HTTPBadRequest)", 400, b"400: Bad Request", "text/plain"),
    ("401 text (HTTPUnauthorized)", 401, b"401: Unauthorized", "text/plain"),
    ("503 empty (HA is_stopping)", 503, b"", None),
    ("400 JSON (the tool's fake)", 400, b'{"message": "Failed to call service"}', "application/json"),
]


def part1():
    state = {"shape": None}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            _, status, body, kind = state["shape"]
            self.send_response(status)
            if kind:
                self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ha = probe.HA(f"http://127.0.0.1:{server.server_address[1]}", "t" * 32, 5)
    out = {}
    try:
        for shape in SHAPES:
            state["shape"] = shape
            try:
                ha.call("light", "turn_on", {"entity_id": "light.x"})
                kind = "no exception"
            except probe.HAUnreachable as exc:
                kind = f"HAUnreachable({exc}) -> Session turns it into Abort(ha_lost): handled"
            except sweep.Refused as exc:
                kind = f"sweep.Refused({exc}) -> escapes Session.run: 'changed nothing'"
            out[shape[0]] = kind
            print(f"RESULT {shape[0]:45s} {kind}")
    finally:
        server.shutdown()
        server.server_close()
    return out


HA_TOKEN, CAM_TOKEN = "h" * 32, "c" * 32
SNAPSHOT = {"light.den_front_left": guard.scene(True, 180, 2700), "light.front_right": guard.scene(False, 120, 3000),
            "light.den_rear_left": guard.scene(True, 90, 2500), "light.den_rear_right": guard.scene(True, 200, 4000)}
STORE = {"version": 1, "minor_version": 1, "key": "den_lighting.state",
         "data": {"version": 1, "enabled": True, "gaming_enabled": False, "gaming": False, "normal": SNAPSHOT,
                  "snapshot": {}, "holds": {}}}


def part2():
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
    H = ha.server.RequestHandlerClass
    original = H.do_POST
    count = {"n": 0}

    def do_POST(self):
        if self.path.startswith("/api/services/light/"):
            count["n"] += 1
            if count["n"] == 20:  # HA is stopping: 503, empty body, nothing applied
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
        return original(self)

    H.do_POST = do_POST
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
            print(f"RESULT e2e exit={code} stderr={err.getvalue().strip()!r}")
            print(f"RESULT e2e session.json phase={record['phase']!r} outcome={record['outcome']!r} "
                  f"restore={record['restore']!r}")
            print(f"RESULT e2e den_lighting automatic switch={switch!r}; bulbs left on={lit}")
            bad = code == 2 and "changed nothing" in err.getvalue() and switch == "off"
            print("FAIL e2e: a 503 empty-body reply (HA stopping) mid-session is reported as 'changed nothing' "
                  "with den_lighting paused" if bad else "PASS e2e: handled")
            return bad
    finally:
        ha.close()
        cam.close()


def main():
    shapes = part1()
    text_handled = all("HAUnreachable" in shapes[k] for k in list(shapes)[:3])
    print("RESULT the finding's cited triggers (500/401 text bodies) are handled as HA lost:", text_handled)
    print("RESULT 503-empty reaches the Refused path:", "Refused" in shapes["503 empty (HA is_stopping)"])
    bad = part2()
    print("RESULT", "FAIL (defect reproduced via the 503-empty trigger)" if bad else "PASS")


if __name__ == "__main__":
    main()
