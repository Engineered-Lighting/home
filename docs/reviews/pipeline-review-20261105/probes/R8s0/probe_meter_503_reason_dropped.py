#!/usr/bin/env python3
"""A POST /meter refused with 503 by the meter build loses the server's reason.

    python3 probe_meter_503_reason_dropped.py <branch-checkout-root>

<root> is a checkout of claude/rung-8-stage0. No network: the meter build's own
Meter.request() (oak-capture-meter.py) answers a hold on a camera whose focus
capability is unknown, and sweep.Sweep.post_meter() is fed that exact reply
through a stand-in camera object. Prints the void reason and detail that
response.json would carry.
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sys.exit(__doc__)
sys.path.insert(0, str(ROOT / "tools/den-light"))
import meter  # noqa: E402
import sweep  # noqa: E402

spec = importlib.util.spec_from_file_location("oak_meter", ROOT / "tools/den-light/oak-capture-meter.py")
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)
m = srv.Meter()
m.attach(lambda spec: None, None)  # meter_autofocus() returned None: focus capability unknown
hold = {"exposure_us": 500, "iso": 100, "white_balance_kelvin": 4000}
status, reply = m.request("/meter", json.dumps(dict(hold, ttl_s=30)).encode())
print(f"RESULT meter build answers {status} {reply}")


class StandIn:
    host, port = "stand-in", 0

    def post(self, path, body):
        return status, reply


run = sweep.Sweep(StandIn(), meter.load_method(), Path("."), say=lambda *a: None)
try:
    run.post_meter(hold)
    print("RESULT no void")
except sweep.Void as void:
    print(f"RESULT sweep void_reason {void.reason!r}, detail {void.detail!r}")
    lost = reply.get("error", "") not in void.detail
    print(("FAIL (defect shown)" if lost else "PASS")
          + ": the server's reason is " + ("not " if lost else "") + "in the recorded detail")
