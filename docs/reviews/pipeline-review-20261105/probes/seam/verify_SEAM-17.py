#!/usr/bin/env python3
"""verify_SEAM-17: does den-core-live's coordinator (decide) accept rung 8 Stage 1's real light-model.json?

    python3 -B verify_SEAM-17.py <wt-root containing den-core-live/ and rung-8-stage1/>

Builds a Stage 1 light-model.json with den-light's own synthetic helpers (verify_stage1_model.py), then runs
den-core's run.py on its own synthetic evening with --policies need incumbent decide and
  (a) --light-model <den-core's own fake(), dumped to JSON>   (control)
  (b) --light-model <Stage 1's light-model.json>
All output in a temp dir. No network, no hardware."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify_stage1_model as V  # noqa: E402

wt = Path(sys.argv[1]).resolve()
core = wt / "den-core-live" / "tools" / "den-core"
tmp = Path(tempfile.mkdtemp(prefix="v17-"))
stage1 = tmp / "stage1-light-model.json"
lm, _ = V.build(wt, stage1)
sys.path.insert(0, str(core))
import light_model as CLM  # noqa: E402
fake = tmp / "fake-light-model.json"
fake.write_text(json.dumps(CLM.fake()), encoding="utf-8")
codes = {}
for label, path in (("den-core fake() (control)", fake), ("Stage 1 light-model.json", stage1)):
    out = tmp / ("run-" + path.stem)
    p = subprocess.run([sys.executable, "-B", str(core / "run.py"), "--out", str(out), "--synthetic", "evening",
                        "--policies", "need", "incumbent", "decide", "--light-model", str(path)],
                       capture_output=True, text=True, cwd=str(tmp))
    last = ((p.stdout + p.stderr).strip().splitlines() or [""])[-1]
    codes[label] = p.returncode
    print("RESULT %s: exit %d: %s" % (label, p.returncode, last[:200]))
try:
    CLM.LightModel(lm)
    print("RESULT den-core LightModel(Stage 1 model): accepted")
except ValueError as e:
    print("RESULT den-core LightModel(Stage 1 model): ValueError:", e)
ok = codes["den-core fake() (control)"] == 0 and codes["Stage 1 light-model.json"] != 0
print("PASS (defect reproduced): decide refuses Stage 1's model" if ok else "FAIL (defect not reproduced)")
print("tmp:", tmp)
