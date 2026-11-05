#!/usr/bin/env python3
"""verify_ha-error-after-pause-says-changed-nothing: does a non-200 HA reply after the pause escape
the abort path and print "This command changed nothing" (exit 2)?

probe.HA.request parses the body as JSON BEFORE it checks the status, so a non-200 reply with a text
body (what real HA gives for a service that raises: aiohttp's "500 Internal Server Error" text) becomes
HAUnreachable -> Abort(ha_lost) -> a --recover line. Only a non-200 with a JSON or EMPTY body reaches
`raise sweep.Refused`. Real HA answers every view with an empty 503 while it is stopping (a restart
mid-session). This probe makes the fake HA answer exactly that from step 4 on, and, as a control,
answers a text-bodied 500 instead.

Fake HA and fake camera on 127.0.0.1, temp dirs only.
    python3 -B verify_ha-error-after-pause-says-changed-nothing.py <root of the claude/rung-8-stage1 checkout>
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import meter  # noqa: E402
import model  # noqa: E402
import probe  # noqa: E402
import synthetic  # noqa: E402
import test_stage1 as T  # noqa: E402


class _Case(unittest.TestCase):
    def runTest(self):
        pass


def run(mode):
    case = _Case()
    rig = T.Rig(case)
    handler = rig.ha.server.RequestHandlerClass
    real_ok = handler.ok
    flag = {"on": False}

    def ok(self):
        if flag["on"]:
            if mode == "503-empty":
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                body = b"500 Internal Server Error\n\nServer got itself in trouble"
                self.send_response(500)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            return False
        return real_ok(self)
    handler.ok = ok
    real_on_step = None

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        private = tmp / "private"
        private.mkdir()
        (private / "ha-token.txt").write_text(T.HA_TOKEN)
        (private / "camera-token.txt").write_text(T.CAM_TOKEN)
        (private / "den_lighting.state").write_text(json.dumps(T.store_for(T.SNAPSHOT)))
        s0 = tmp / "stage0-x" / "sweep-1"
        s0.mkdir(parents=True)
        params, digest = synthetic.meter_norm_for(rig.m0)
        (s0 / "response.json").write_text(json.dumps({"outcome": "pass", "meter_norm": {
            "version": meter.METER_NORM_VERSION, "parameters": params, "parameters_sha256": digest}}))
        session = tmp / "s1-probe"
        real_init = probe.Session.__init__

        def init(self, *a, **k):
            real_init(self, *a, **k)
            self.on_step = T.at_step(4, lambda _s: flag.__setitem__("on", True))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(model, "load_stage1", lambda path=None: rig.m1), \
                mock.patch.object(meter, "load_method", lambda *a, **k: rig.m0), \
                mock.patch.object(probe.Session, "__init__", init), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = probe.main(["--session", str(session), "--ha", rig.ha.url, "--camera", rig.cam.url,
                               "--stage0", str(s0), "--store-copy", str(private / "den_lighting.state"),
                               "--ha-token-file", str(private / "ha-token.txt"),
                               "--camera-token-file", str(private / "camera-token.txt")])
        record = json.loads((session / "session.json").read_text())
        switch = rig.ha.states[synthetic.SWITCH]["state"]
        print(f"RESULT [{mode}] exit {code}; stdout {out.getvalue().strip()!r}; stderr {err.getvalue().strip()!r}")
        print(f"RESULT [{mode}] session.json phase {record.get('phase')} outcome {record.get('outcome')}; "
              f"automatic switch {switch}; holds {sorted(rig.ha.store()['data']['holds'])}")
        bad = code == 2 and "changed nothing" in err.getvalue() and switch == "off"
        print(("FAIL" if bad else "PASS") + f" [{mode}]: " +
              ("'changed nothing', exit 2, den_lighting left paused, no --recover line" if bad else
               "handled as an abort (exit/--recover line above)"))
    handler.ok = real_ok
    case.doCleanups()


if __name__ == "__main__":
    run("503-empty")
    run("500-text")
