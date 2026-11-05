#!/usr/bin/env python3
"""verify-intent_meter-503-reason-dropped.py: does a POST /meter 503 keep the meter server's reason?

End to end, no real host: copies tools/den-light to a temp dir, starts the suite's own FakeCamera on
127.0.0.1, and routes its POST /meter through the committed meter server's Meter.request()
(oak-capture-meter.py) with the focus capability unknown (meter_autofocus() returned None), i.e. what
the real meter build answers when getConnectedCameraFeatures() raises. Then runs sweep.Sweep as
`--run` does, three times (the README's 3 reruns), and prints the void reason/detail in response.json
and the line the owner sees. Also checks the second deterministic 503 ("no lens position reported yet")
and the 400 path.

How to run:
  python3 -B verify-intent_meter-503-reason-dropped.py <root of claude/rung-8-stage0 checkout>
Prints RESULT lines, then FAIL (reason lost) or PASS (reason kept).
"""
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import contextlib
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(sys.argv[1]).resolve()
tmp = Path(tempfile.mkdtemp(prefix="r8s0-503-"))
shutil.copytree(root / "tools" / "den-light", tmp / "tools" / "den-light")
(tmp / "tools" / "fixtures").mkdir()
shutil.copy(root / "tools/fixtures/installed-oak-capture-20261016.py", tmp / "tools/fixtures/")
tool = tmp / "tools" / "den-light"
sys.path.insert(0, str(tool))
import meter  # noqa: E402
import sweep  # noqa: E402
import test_den_light as T  # noqa: E402

spec = importlib.util.spec_from_file_location("oak_meter", tool / "oak-capture-meter.py")
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)


def run_case(label, autofocus, lens_known, runs=1):
    fake = T.FakeCamera()
    m = srv.Meter()
    m.attach(lambda s: None, autofocus)
    if lens_known:
        m.lens_position = 120
    orig_handler = fake.server.RequestHandlerClass
    base_post = orig_handler.do_POST

    def do_POST(self):
        if self.path != "/meter":
            return base_post(self)
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        status, reply = m.request("/meter", raw)
        body = json.dumps(reply).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    orig_handler.do_POST = do_POST
    server_reason = m.request("/meter", json.dumps({"exposure_us": 500, "iso": 100, "white_balance_kelvin": 4000,
                                                     "ttl_s": 30}).encode())
    print(f"RESULT {label}: meter build POST /meter answers {server_reason[0]} {server_reason[1]}")
    lost_all = True
    try:
        for i in range(runs):
            with T.stage0_folder() as (stage0, token):
                camera = sweep.Camera(fake.url, T.TOKEN)
                _, folder = sweep.start_run(stage0, camera, token, T.sweep_method())
                response = sweep.Sweep(camera, T.sweep_method(), folder, say=lambda *a, **k: None).run()
                on_disk = json.loads((folder / "response.json").read_text())
            text = json.dumps(on_disk)
            reason = server_reason[1].get("error", "")
            kept = reason in text
            lost_all = lost_all and not kept
            print(f"RESULT {label} run {i + 1}: outcome={on_disk['outcome']} void_reason={on_disk['void_reason']!r} "
                  f"detail={on_disk['detail']!r}; server reason in response.json: {kept}")
            if i == 0:
                gone = "The frames were deleted." if on_disk.get("frames_deleted") else "frames left"
                print(f"RESULT {label} owner sees: VOID ({on_disk['void_reason']}): {on_disk['detail']}. {gone}")
        health = [r for r in fake.requests if r[1] == "/health"]
        print(f"RESULT {label}: /health calls answered by the fake (healthy): {len(health)}")
    finally:
        fake.close()
    return lost_all


a = run_case("focus-unknown", None, False, runs=3)
b = run_case("no-lens-yet", True, False, runs=1)
print("FAIL (reason lost): the meter server's 503 reason never reaches response.json" if (a and b)
      else "PASS: the reason is kept")
shutil.rmtree(tmp, ignore_errors=True)
