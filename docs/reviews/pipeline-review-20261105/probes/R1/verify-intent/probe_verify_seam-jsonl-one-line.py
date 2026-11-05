#!/usr/bin/env python3
"""Verifier probe for seam-rung2-refuses-entry: which part of the seam is rung 1's, which rung 2's?

1. Registers once with den-register (synthetic scene, proposed values closed) and hands the
   room-model.jsonl file AS WRITTEN (one line) to den-observe's roommodel.load + Camera.
   If it reads, the JSON-Lines-vs-one-object failure only appears from the second revision.
2. Prints the camera_entry_form slot from den-observe's METHOD.md and whether den-register's
   REGISTER-V1.md block has any slot for the camera entry's inner form.

Synthetic data and temp folders only; nothing contacts a host.
Run:  python3 -B probe_verify_seam-jsonl-one-line.py <rung-1 root> <rung-2 root>
"""
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

root1, root2 = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
tool1, tool2 = root1 / "tools" / "den-register", root2 / "tools" / "den-observe"
sys.path.insert(0, str(tool1))
import method, register, synthetic  # noqa: E402

text = (tool1 / "REGISTER-V1.md").read_text(encoding="utf-8")
closed = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", r"\1 = \2  # closed for the probe", text, flags=re.M)
M = method.parse(closed)
RUNG2 = r'''
import sys
sys.path.insert(0, sys.argv[1])
import roommodel
try:
    model = roommodel.load(sys.argv[2])
    print("LOADED one object with", len(model.get("cameras", [])), "camera entries")
    roommodel.Camera(model, sys.argv[3], max_error_bound_m=0.05)
    print("ACCEPTED")
except roommodel.RegistrationRefused as exc:
    print("REFUSED", exc)
'''
with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    depth, left, seen = synthetic.render(synthetic.POINTS)
    take = synthetic.write_take(tmp / "take1", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = tmp / "work"
    work.mkdir()
    (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet()), encoding="utf-8")
    ws = register.worksheet_file.load(work / "worksheet.json", need_positions=False)
    s = register.ClickSession(work, take, register.frame_number(take, None, work), ws, M)
    for pid, (u, v, _) in seen.items():
        s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    s.done()
    code, rep = register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
    n_lines = len((work / "room-model.jsonl").read_text(encoding="utf-8").splitlines())
    print(f"rung 1: {rep['result']} {rep['transform_id']}; room-model.jsonl has {n_lines} line(s)")
    out = subprocess.run([sys.executable, "-I", "-c", RUNG2, str(tool2), str(work / "room-model.jsonl"),
                          rep["transform_id"]], capture_output=True, text=True, timeout=60)
    print("rung 2 on room-model.jsonl as written:", (out.stdout + out.stderr).strip().replace("\n", " | "))
slot2 = [l for l in (tool2 / "METHOD.md").read_text(encoding="utf-8").splitlines() if l.startswith("camera_entry_form")]
print("rung 2 slot:", slot2)
block1 = re.findall(r"```register-v1\n(.*?)```", text, re.S)[0]
print("rung 1 slot names:", [l.split("=")[0].strip() for l in block1.splitlines() if "=" in l])
print("RESULT INFO")
