#!/usr/bin/env python3
"""probe_refused_after_pause: an HA error reply after the pause escapes the restore and is printed as
"This command changed nothing" (exit 2), while den_lighting is left paused with the probe's levels as
its normal scene.

What it checks: probe.HA.request raises sweep.Refused for any non-200 reply. Session.run() only
catches Abort after the pause, and probe.main() maps sweep.Refused to "Refused: ... This command
changed nothing." and exit 2. Real HA answers a service call whose integration raises
HomeAssistantError (a ZHA bulb that does not acknowledge) with a non-200 status; the fake HA answers 400.

How to run (synthetic data, fakes on 127.0.0.1 only, temp dirs only):
    python3 -B probe_refused_after_pause.py <root of the claude/rung-8-stage1 checkout>
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
import guard  # noqa: E402
import meter  # noqa: E402
import model  # noqa: E402
import probe  # noqa: E402
import synthetic  # noqa: E402
import test_stage1 as T  # noqa: E402


class _Case(unittest.TestCase):
    def runTest(self):
        pass


def main():
    case = _Case()
    rig = T.Rig(case)
    real_call = rig.ha.call
    count = {"light": 0}

    def failing(domain, service, data):
        if domain == "light":
            count["light"] += 1
            if count["light"] == 6:  # step 1 (all-off), after the pause and the exposure pick
                raise synthetic.HAError("Failed to send request: device did not respond")
        return real_call(domain, service, data)
    rig.ha.call = failing

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
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(model, "load_stage1", lambda path=None: rig.m1), \
                mock.patch.object(meter, "load_method", lambda *a, **k: rig.m0), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = probe.main(["--session", str(session), "--ha", rig.ha.url, "--camera", rig.cam.url,
                               "--stage0", str(s0), "--store-copy", str(private / "den_lighting.state"),
                               "--ha-token-file", str(private / "ha-token.txt"),
                               "--camera-token-file", str(private / "camera-token.txt")])
        record = json.loads((session / "session.json").read_text())
        switch = rig.ha.states[synthetic.SWITCH]["state"]
        store = rig.ha.store()["data"]
        normal = {e: T.norm(v) for e, v in store["normal"].items()}
        changed = [e for e in guard.LIGHTS if not guard.hold_ok(T.SNAPSHOT[e], normal[e])]
        lit = {e: (rig.ha.lights[e]["on"], rig.ha.lights[e]["brightness"], rig.ha.lights[e]["kelvin"])
               for e in guard.LIGHTS}
        rest = [(c[1], c[2]) for c in rig.ha.calls if c[5] == "rest"]
        print("RESULT exit code:", code)
        print("RESULT stderr:", err.getvalue().strip())
        print("RESULT session.json phase/outcome:", record.get("phase"), record.get("outcome"))
        print("RESULT den_lighting automatic switch:", switch)
        print("RESULT den_lighting holds:", sorted(store["holds"]))
        print("RESULT bulbs whose den_lighting normal no longer equals the snapshot:", changed)
        print("RESULT bulbs now (on, brightness, kelvin):", lit)
        print("RESULT last REST calls:", rest[-3:])
        print("RESULT any den_lighting.resume_automatic sent:", any(d == "den_lighting" for d, _ in rest))
        says_nothing = "changed nothing" in err.getvalue()
        paused = switch == "off"
        if code == 2 and says_nothing and paused and changed:
            print("FAIL: the tool printed 'This command changed nothing' (exit 2 = refused) but den_lighting "
                  "is still paused, holds every probed bulb and has the probe's levels as its normal scene; "
                  "no restore and no resume were attempted")
        else:
            print("PASS: an HA error after the pause was handled as an abort with a restore")
    case.doCleanups()


if __name__ == "__main__":
    main()
