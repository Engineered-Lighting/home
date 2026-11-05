#!/usr/bin/env python3
"""verify_recheck-accepts-post-hoc-changes.py: independent re-check of finding recheck-accepts-post-hoc-changes,
case 2 only, made deterministic (the reporter's probe injects the off-hold ISO into the 40th held frame,
which can land on a settling frame that is skipped, so its case 2 saved a PASS in one run).

What it checks (rung 8 Stage 0, tools/den-light/sweep.py):
  Every held frame of step 5 reports ISO + 1, so settling never succeeds and the step keeps frames that
  fail gain agreement: the sweep saves FAIL ['gain agreement'] deterministically. Then ONLY frames.jsonl
  is edited (those reports' ISO set back to the hold), no frame bytes. `sweep.py --recheck` is run via
  sweep.main(). Prints its exit code and every printed line, and the recheck record's same_as_saved.

How to run:
  python3 -B verify_recheck-accepts-post-hoc-changes.py <root of the claude/rung-8-stage0 checkout>

Synthetic data only: the suite's own FakeCamera on 127.0.0.1 and temp folders; tools/den-light is copied
to a temp dir first, nothing in the checkout is modified. No real host is contacted.
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
    tmp = Path(tempfile.mkdtemp(prefix="r8s0-vrecheck-"))
    try:
        tool = tmp / "tools" / "den-light"
        shutil.copytree(root / "tools" / "den-light", tool)
        (tmp / "tools" / "fixtures").mkdir()
        shutil.copy(root / "tools/fixtures/installed-oak-capture-20261016.py", tmp / "tools/fixtures/")
        text = (tool / "METHOD-V1.md").read_text(encoding="utf-8").replace("# open", "# confirmed")
        for name, value in dict(countdown_s=0, still_span_s=0.15, still_wait_limit_s=3, poll_s=0.005,
                                settle_limit_s=0.5, frames_per_step=2, run_limit_s=90, pixel_step=20,
                                fit_iterations=10, region_min_code_samples=3,
                                region_tolerance_percent=10).items():
            text = set_number(text, name, value)
        (tool / "METHOD-V1.md").write_text(text, encoding="utf-8")
        sys.path.insert(0, str(tool))
        import meter, sweep, test_den_light as T  # noqa: E402  (the copies in tmp)
        stage0 = tmp / "den-light" / "stage0-20261110"
        stage0.mkdir(parents=True)
        token = tmp / "private" / "camera-token.txt"
        token.parent.mkdir()
        token.write_text(T.TOKEN)
        method = meter.load_method()
        assert not method.open
        (stage0 / "install.json").write_text(json.dumps(
            {"schema": sweep.SCHEMAS["install"], "meter_sha256": sweep.METER_SERVER_SHA256}))
        fake = T.FakeCamera()
        camera = sweep.Camera(fake.url, T.TOKEN)
        try:
            real = meter.rgb_report
            plan = {}

            def off_iso(meta):
                rep = real(meta)
                # every held frame of the 5th step's hold reports ISO + 1
                if rep["held"] and plan.get("hold5") and rep["exposure_us"] is not None \
                        and abs(rep["exposure_us"] - plan["hold5"]["exposure_us"]) <= 60:
                    rep["iso"] = rep["iso"] + 1
                return rep
            real_plan = sweep.exposure_plan

            def spy_plan(*a, **k):
                p = real_plan(*a, **k)
                plan["hold5"] = p[4]
                return p
            with mock.patch.object(sweep.meter, "rgb_report", off_iso), \
                    mock.patch.object(sweep, "exposure_plan", spy_plan):
                _, folder = sweep.start_run(stage0, camera, token, method)
                saved = sweep.Sweep(camera, method, folder, say=lambda *a, **k: None).run()
            reasons = saved.get("check", {}).get("reasons")
            print(f"saved outcome {saved['outcome']} reasons {reasons}")
            lines, edited, changed = (folder / "frames.jsonl").read_text().splitlines(), [], 0
            for raw in lines:
                line = json.loads(raw)
                if line["report"]["iso"] != line["hold"]["iso"]:
                    line["report"]["iso"] = line["hold"]["iso"]
                    changed += 1
                edited.append(json.dumps(line, sort_keys=True))
            (folder / "frames.jsonl").write_text("\n".join(edited) + "\n")
            print(f"edited {changed} report(s) in frames.jsonl; no frame bytes changed")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = sweep.main(["--recheck", str(folder)])
            rec = json.loads((folder / "recheck-1.json").read_text())
            print(f"--recheck exit {code}; printed: {out.getvalue().strip()!r}")
            print(f"recheck-1.json: outcome={rec['outcome']} same_as_saved={rec['same_as_saved']} "
                  f"method_matches_sweep={rec['method_matches_sweep']}")
            ok = saved["outcome"] == "fail" and reasons == ["gain agreement"] and code == 0
            print("RESULT case 2 REPRODUCED (FAIL -> RECHECK PASS after a frames.jsonl-only edit)" if ok
                  else "RESULT case 2 not reproduced")
        finally:
            fake.close()
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
