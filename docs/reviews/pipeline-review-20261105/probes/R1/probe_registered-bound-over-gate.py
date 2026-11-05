#!/usr/bin/env python3
"""Probe: den-register REGISTERS while writing error_bound_m above its own 5 cm gate.

error_bound_m is written as the largest of the fit residuals, check errors and
leave-one-out (LOO) errors, but only residuals and checks are gated. With four fit
points and a few cm of tape error, a LOO error can exceed 5 cm while every residual
and check is within 5 cm: the tool prints REGISTERED and writes error_bound_m > 0.05.
den-observe (rung 2) refuses any entry whose error_bound_m is above 0.05 ("above rung
1's 0.05 m gate"), so a registration rung 1 accepted is refused downstream.

Synthetic scene only (den-register's synthetic.py), depth quantised to 1 mm as R's
bundles are. The rung-2 refusal is reproduced by calling den-observe's
roommodel.Camera in a subprocess, with the den-observe-camera-1 fields filled in by
hand so the bound is the only reason left.

Run:  python3 probe_registered-bound-over-gate.py <rung-1 checkout root> [<rung-2 checkout root>]
"""
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

root1 = Path(sys.argv[1]).resolve()
root2 = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else root1.parent / "rung-2-observe"
tool = root1 / "tools" / "den-register"
sys.path.insert(0, str(tool))
import method, register, synthetic  # noqa: E402

text = (tool / "REGISTER-V1.md").read_text(encoding="utf-8")
text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", r"\1 = \2  # closed for the probe", text, flags=re.M)
M = method.parse(text)
PATTERN = {"p1": [0.3, -0.2, 0.1], "p2": [-0.1, 0.3, -0.2], "p3": [0.2, 0.1, 0.3], "p4": [-0.3, -0.1, 0.0]}


def fit_with(scale, tmp):
    depth, left, seen = synthetic.render(synthetic.POINTS)
    take = synthetic.write_take(tmp / "take1", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = tmp / "work"
    work.mkdir()
    sheet = synthetic.worksheet(offsets={k: [scale * x for x in d] for k, d in PATTERN.items()})
    (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
    ws = register.worksheet_file.load(work / "worksheet.json", need_positions=False)
    s = register.ClickSession(work, take, register.frame_number(take, None, work), ws, M)
    for pid, (u, v, _) in seen.items():
        s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    s.done()
    code, report = register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
    return work, code, report


RUNG2 = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import roommodel
try:
    cam = roommodel.Camera(roommodel.load(sys.argv[2]), sys.argv[3], max_error_bound_m=0.05)
    print("ACCEPTED", cam.transform_id)
except roommodel.RegistrationRefused as exc:
    print("REFUSED", exc)
'''

hit = None
for step in range(1, 40):
    scale = 0.005 * step
    with tempfile.TemporaryDirectory() as t:
        work, code, report = fit_with(scale, Path(t))
        if report["result"] != "registered":
            continue
        fit = report["fit"]
        if fit["error_bound_m"] > 0.05:
            line = (work / "room-model.jsonl").read_text(encoding="utf-8").splitlines()[-1]
            model = json.loads(line)
            cam = model["cameras"][-1]
            k = cam["intrinsics"]
            kk = {"fx": k["fx"], "fy": k["fy"], "cx": k["cx"], "cy": k["cy"]}
            cam["intrinsics"] = {"source": "probe", "rectified_left": kk, "rgb": kk, "rgb_distortion": [0.0] * 8,
                                 "rgb_from_rectified_left": {"rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                                             "translation_m": [0.0375, 0.0, 0.0]},
                                 "stereo_baseline_m": 0.075, "depth_size": [640, 400], "rgb_size": [640, 360]}
            cam["pose"]["camera_frame"] = "rectified_left"
            path = Path(t) / "filled.json"
            path.write_text(json.dumps(model) + "\n", encoding="utf-8")
            r2 = subprocess.run([sys.executable, "-I", "-c", RUNG2, str(root2 / "tools" / "den-observe"), str(path),
                                 report["transform_id"]], capture_output=True, text=True, timeout=60)
            hit = (scale, code, report, (r2.stdout + r2.stderr).strip(), cam["error_bound_m"])
            break

if not hit:
    print("RESULT PASS: no registered fit with error_bound_m above 5 cm")
    sys.exit(0)
scale, code, report, rung2, written = hit
fit = report["fit"]
worst_check = max(p["error_m"] for p in report["pairs"] if "error_m" in p)
print(f"tape-error scale {scale:.3f}: exit {code}, {report['result'].upper()} {report['transform_id']}")
print(f"  fit residual max {fit['max_m'] * 100:.1f} cm, worst check {worst_check * 100:.1f} cm, "
      f"leave-one-out max {fit['loo_max_m'] * 100:.1f} cm -> error_bound_m written {written}")
print(f"  rung 2 roommodel.Camera on that entry: {rung2}")
print("FAIL: a registration rung 1 accepted carries error_bound_m above the gate, and rung 2 refuses it")
print("RESULT FAIL")
