#!/usr/bin/env python3
"""probe_recheck-accepts-post-hoc-changes.py

What it checks (rung 8 Stage 0, tools/den-light):
  Case 1. A sweep is saved with outcome FAIL under one METHOD-V1 Stage 0 block. The block is then
          loosened (residual_max_codes raised) and `sweep.py --recheck <sweep folder>` is run through
          sweep.main(). Does it refuse, or at least say the METHOD changed? Or does it print
          "RECHECK PASS" and exit 0?
  Case 2. A sweep is saved with outcome FAIL (one kept frame reports an ISO off the hold). Only
          frames.jsonl is edited afterwards (that frame's report), no frame bytes. Does --recheck
          notice, or does it print "RECHECK PASS" and exit 0?
  Case 3. install.json records the METHOD Stage 0 block sha256 it was made under. After the block
          changes, does `--run`'s start_run() refuse, or does it create a new sweep folder?

How to run:
  python3 -B probe_recheck-accepts-post-hoc-changes.py <root of the claude/rung-8-stage0 checkout>

Synthetic data only: the suite's own FakeCamera on 127.0.0.1 and temp folders. Nothing in the
checkout is modified (tools/den-light is copied to a temp dir first). No real host is contacted.
Prints PASS/FAIL/RESULT lines; exit 0 means the probe reproduced the defect (each case shows the
tool accepting the post-hoc change).
"""
import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock


def set_number(text, name, value):
    new, count = re.subn(rf"^{name} = [^\s#]+", f"{name} = {value}", text, flags=re.M)
    assert count == 1, name
    return new


def main():
    root = Path(sys.argv[1]).resolve()
    tmp = Path(tempfile.mkdtemp(prefix="r8s0-probe-"))
    try:
        tool = tmp / "tools" / "den-light"
        shutil.copytree(root / "tools" / "den-light", tool)
        (tmp / "tools" / "fixtures").mkdir()
        shutil.copy(root / "tools/fixtures/installed-oak-capture-20261016.py", tmp / "tools/fixtures/")
        method_file = tool / "METHOD-V1.md"
        text = method_file.read_text(encoding="utf-8")
        text = text.replace("# open", "# confirmed")          # the owner confirmed every number
        for name, value in dict(countdown_s=0, still_span_s=0.15, still_wait_limit_s=3, poll_s=0.005,
                                settle_limit_s=1.0, frames_per_step=2, run_limit_s=60, pixel_step=20,
                                fit_iterations=10, region_min_code_samples=3,
                                region_tolerance_percent=10).items():
            text = set_number(text, name, value)
        tight = set_number(text, "residual_max_codes", 0.05)  # R as frozen for the sweep
        method_file.write_text(tight, encoding="utf-8")
        sys.path.insert(0, str(tool))
        import meter, sweep, test_den_light as T  # noqa: E402  (the copies in tmp)

        stage0 = tmp / "den-light" / "stage0-20261110"
        stage0.mkdir(parents=True)
        token = tmp / "private" / "camera-token.txt"
        token.parent.mkdir()
        token.write_text(T.TOKEN)
        m1 = meter.load_method()
        assert not m1.open, m1.open
        (stage0 / "install.json").write_text(json.dumps({
            "schema": sweep.SCHEMAS["install"], "meter_sha256": sweep.METER_SERVER_SHA256,
            "method_sha256": m1.file_sha256, "method_stage0_block_sha256": m1.block_sha256}))
        fake = T.FakeCamera()
        camera = sweep.Camera(fake.url, T.TOKEN)
        results = []
        try:
            # ---- case 1: a saved FAIL, then METHOD loosened, then --recheck
            _, folder1 = sweep.start_run(stage0, camera, token, m1)
            r1 = sweep.Sweep(camera, m1, folder1, say=lambda *a, **k: None).run()
            print(f"case 1: saved outcome {r1['outcome']} reasons {r1.get('check', {}).get('reasons')}")
            method_file.write_text(set_number(tight, "residual_max_codes", 4), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = sweep.main(["--recheck", str(folder1)])
            rec = json.loads((folder1 / "recheck-1.json").read_text())
            printed = out.getvalue().strip().replace("\n", " | ")
            print(f"case 1: --recheck exit {code}; printed: {printed}")
            print(f"case 1: recheck-1.json outcome={rec['outcome']} method_matches_sweep={rec['method_matches_sweep']}"
                  f" same_as_saved={rec['same_as_saved']}")
            bad1 = (r1["outcome"] == "fail" and code == 0 and "RECHECK PASS" in printed
                    and rec["method_matches_sweep"] is False and "METHOD" not in printed.upper().replace("RECHECK", ""))
            results.append(bad1)
            print(("RESULT case 1 REPRODUCED: a loosened METHOD turns a saved FAIL into 'RECHECK PASS', exit 0, "
                   "and the printed lines never say the METHOD changed") if bad1 else "RESULT case 1 not reproduced")

            # ---- case 3: --run after the METHOD changed since install.json
            m2 = meter.load_method()
            try:
                _, folder3 = sweep.start_run(stage0, camera, token, m2)
                bad3 = True
                print(f"case 3: install.json block {m1.block_sha256[:12]} != current block {m2.block_sha256[:12]}; "
                      f"start_run created {folder3.name} without refusing")
            except sweep.Refused as exc:
                bad3 = False
                print(f"case 3: start_run refused: {exc}")
            results.append(bad3)
            print("RESULT case 3 REPRODUCED: --run accepts a METHOD block that differs from the one install.json "
                  "recorded" if bad3 else "RESULT case 3 not reproduced")
            if bad3:
                shutil.rmtree(folder3)

            # ---- case 2: a saved FAIL from one ISO off the hold; only frames.jsonl edited
            real = meter.rgb_report
            state = {"n": 0}

            def off_iso(meta):
                rep = real(meta)
                if rep["held"]:
                    state["n"] += 1
                    if state["n"] == 40:            # one held frame reports ISO + 1
                        rep["iso"] = rep["iso"] + 1
                return rep
            with mock.patch.object(sweep.meter, "rgb_report", off_iso):
                _, folder2 = sweep.start_run(stage0, camera, token, m2)
                r2 = sweep.Sweep(camera, m2, folder2, say=lambda *a, **k: None).run()
            reasons = r2.get("check", {}).get("reasons")
            print(f"case 2: saved outcome {r2['outcome']} reasons {reasons}")
            lines = (folder2 / "frames.jsonl").read_text().splitlines()
            edited = []
            for raw in lines:
                line = json.loads(raw)
                hold = line["hold"]
                if line["report"]["iso"] != hold["iso"]:
                    line["report"]["iso"] = hold["iso"]
                edited.append(json.dumps(line, sort_keys=True))
            (folder2 / "frames.jsonl").write_text("\n".join(edited) + "\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code2 = sweep.main(["--recheck", str(folder2)])
            printed2 = out.getvalue().strip().replace("\n", " | ")
            print(f"case 2: --recheck exit {code2}; printed: {printed2}")
            bad2 = r2["outcome"] == "fail" and reasons == ["gain agreement"] and code2 == 0 and "RECHECK PASS" in printed2
            results.append(bad2)
            print("RESULT case 2 REPRODUCED: an edited frames.jsonl (no frame bytes changed) turns a saved FAIL into "
                  "'RECHECK PASS', exit 0" if bad2 else "RESULT case 2 not reproduced")
        finally:
            fake.close()
        print("PASS (defect reproduced in every case)" if all(results) else
              f"FAIL (reproduced {sum(results)} of {len(results)} cases)")
        return 0 if all(results) else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
