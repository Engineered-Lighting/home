#!/usr/bin/env python3
"""verify_meter-503-reason-dropped.py: end to end, does a POST /meter 503's reason reach response.json?

A fake camera on 127.0.0.1 (the suite's own FakeCamera) answers /health 200 and serves good bundles,
but answers every POST /meter with the meter build's own reply for a camera whose focus capability is
unknown: the status and JSON body come from oak-capture-meter.py's Meter.request() after
attach(send, None), i.e. what meter_autofocus() returning None produces. The real sweep.Sweep.run()
is then run through real HTTP three times in a row (the README's "up to 3 reruns"), and each
response.json on disk is read back.

How to run:
  python3 -B verify_meter-503-reason-dropped.py <root of claude/rung-8-stage0 checkout>
Synthetic data only; temp dirs only; nothing binds beyond 127.0.0.1; the checkout is not modified.
"""
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sys.exit(__doc__)
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "tools/den-light"))
import sweep  # noqa: E402
import test_den_light as t  # noqa: E402

srv_meter = t.SRV.Meter()
srv_meter.attach(lambda spec: None, None)  # meter_autofocus() gave None


class FocusUnknownCamera(t.FakeCamera):
    def handler(self):
        base = super().handler()

        class Handler(base):
            def do_POST(self):
                if self.path == "/meter" and self.headers.get("Authorization") == "Bearer " + t.TOKEN:
                    raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    status, reply = srv_meter.request("/meter", raw)
                    return self.reply(status, json.dumps(reply).encode())
                return super().do_POST()
        return Handler


fake = FocusUnknownCamera()
try:
    camera = sweep.Camera(fake.url, t.TOKEN)
    hs, hbody = camera.get("/health")
    print(f"RESULT GET /health answers {hs} {hbody.decode()}")
    details = []
    for attempt in range(1, 4):
        method = t.sweep_method()
        with t.stage0_folder() as (stage0, token):
            _, folder = sweep.start_run(stage0, camera, token, method)
            run = sweep.Sweep(camera, method, folder, say=t.quiet)
            run.run()
            on_disk = json.loads((folder / "response.json").read_text())
        status, reply = srv_meter.request("/meter", json.dumps(
            {"exposure_us": 8000, "iso": 100, "white_balance_kelvin": 4600, "ttl_s": 30}).encode())
        print(f"RESULT run {attempt}: server said {status} {reply}; response.json outcome={on_disk['outcome']!r} "
              f"void_reason={on_disk['void_reason']!r} detail={on_disk['detail']!r}")
        details.append((on_disk["outcome"], on_disk["void_reason"], on_disk["detail"]))
    lost = all(reply["error"] not in d[2] for d in details)
    same = len(set(details)) == 1
    print(("FAIL (defect shown)" if lost else "PASS") + f": the 503's reason is {'absent from' if lost else 'in'} "
          f"every response.json; all three runs identical={same}")
finally:
    fake.close()
