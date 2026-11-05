#!/usr/bin/env python3
"""verify_stage0-failed-sweep-accepted-via-recheck: end to end, with den-light's own fake camera
(127.0.0.1 only, from test_den_light.FakeCamera) and temp folders only.

What it checks:
 1. A Stage 0 sweep run with residual_max_codes too tight writes response.json outcome "fail".
 2. sweep.recheck() on that folder with an EDITED Stage 0 block (residual_max_codes loosened, so the
    block sha256 differs) writes recheck-1.json with outcome "pass" and method_matches_sweep false.
 3. probe.stage0_meter_norm() (what probe.py --session and model.py call) returns recheck-1.json.
 4. With response.json "pass" and a newer recheck "fail" (as a corrected fit would write), it still
    returns response.json.

How to run:
    python3 -B verify_stage0-failed-sweep-accepted-via-recheck.py <root of the claude/rung-8-stage1 checkout>
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import meter  # noqa: E402
import probe  # noqa: E402
import sweep  # noqa: E402
import test_den_light as T  # noqa: E402


def main():
    fake = T.FakeCamera()
    try:
        tight = T.sweep_method(residual_max_codes=0.0)
        tight = meter.Method(tight.values, tight.open, "f" * 64, "a" * 64)   # the block the sweep ran under
        with T.stage0_folder() as (stage0, token):
            camera = sweep.Camera(fake.url, T.TOKEN)
            _, folder = sweep.start_run(stage0, camera, token, tight)
            response = sweep.Sweep(camera, tight, folder, say=T.quiet).run()
            print("RESULT sweep outcome:", response["outcome"], "| reasons:", response.get("check", {}).get("reasons"))
            edited = meter.Method({**tight.values, "residual_max_codes": 1000.0}, tight.open, "e" * 64, "b" * 64)
            record, path = sweep.recheck(folder, edited)
            print("RESULT recheck:", path.name, "outcome", record["outcome"], "method_matches_sweep",
                  record["method_matches_sweep"], "same_as_saved", record["same_as_saved"])
            norm, source = probe.stage0_meter_norm(folder)
            print("RESULT stage0_meter_norm source:", source)
            ok1 = not (response["outcome"] == "fail" and record["outcome"] == "pass"
                       and record["method_matches_sweep"] is False and source == "recheck-1.json")
            print("FAIL: a failed sweep is accepted through a recheck under an edited Stage 0 block" if not ok1
                  else "PASS (part 1 did not reproduce)")
    finally:
        fake.close()
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "response.json").write_text(json.dumps({"outcome": "pass", "meter_norm": {"tag": "orig"}}))
        (folder / "recheck-1.json").write_text(json.dumps({"outcome": "fail", "method_matches_sweep": True,
                                                           "same_as_saved": False, "meter_norm": {"tag": "r1"}}))
        norm, source = probe.stage0_meter_norm(folder)
        print("RESULT saved pass + newer recheck fail ->", source)
        print("FAIL: a newer failing recheck does not withdraw the saved pass" if source == "response.json"
              else "PASS (part 2 did not reproduce)")


if __name__ == "__main__":
    main()
