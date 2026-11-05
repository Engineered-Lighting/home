#!/usr/bin/env python3
"""Verifier probe for SEAM-08: does an older transform_id stop resolving after a re-registration?

On synthetic data in a temp dir, den-register registers twice (second fit with one
click moved), giving room-model.jsonl with revisions den-v1 and den-v2. Then:
  1. lists each revision's camera entries (the reported observation: den-v2 holds only .2);
  2. runs den-register's own `register.py resolve <work> <old transform_id>` CLI
     (the documented lookup) -> expect exit 0: the old id resolves over the versioned file;
  3. checks every transform_id in the file names exactly one distinct entry
     (records.resolve over all revisions);
  4. counts entries per camera in the newest revision (den-photometry, rung 7, refuses a
     RoomModel unless it holds exactly one entry for its camera: photometry.py:617).
PASS on 2-4 means the reported failure needs a consumer that reads only the newest
revision and must resolve an old id; no such consumer is in rungs 2-10.

Run:
  TMPDIR=<scratch tmp> python3 -B probe_SEAM-08_spec.py <rung-1-register root>
"""
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

R1 = Path(sys.argv[1]).resolve()
REG = R1 / "tools" / "den-register"
work = Path(tempfile.mkdtemp(prefix="probe-seam-08-"))
print(f"RESULT work folder {work}")

REGISTER = textwrap.dedent(r'''
import io, json, re, sys
from pathlib import Path
REG = Path(sys.argv[1]); out = Path(sys.argv[2])
sys.path.insert(0, str(REG))
import method, register, synthetic, worksheet, records
text = (REG / "REGISTER-V1.md").read_text(encoding="utf-8")
text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}  # closed for the probe", text, flags=re.M)
m = method.parse(text)
depth, left, seen = synthetic.render(synthetic.POINTS)
take = synthetic.write_take(out / "take1", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
w = out / "register"; w.mkdir()
(w / "worksheet.json").write_text(json.dumps(synthetic.worksheet(synthetic.POINTS, synthetic.FIT, synthetic.CHECKS, None)), encoding="utf-8")
def click(shift=None):
    s = register.ClickSession(w, take, register.frame_number(take, None, w), worksheet.load(w / "worksheet.json", need_positions=False), m)
    for pid, (u, v, _) in seen.items():
        du, dv = (shift or {}).get(pid, (0, 0))
        s.click({"id": pid, "u": round(u + du, 1), "v": round(v + dv, 1)})
    s.done()
buf = io.StringIO()
click(); c1, r1 = register.run_fit(w, take, m=m, now=lambda: 1_800_000_500.0, out=buf)
click({"p1": (0.4, 0)}); c2, r2 = register.run_fit(w, take, m=m, now=lambda: 1_800_000_900.0, out=buf)
revs = records.read_jsonl(w / "room-model.jsonl", "x")
ids = sorted({records.transform_id(c["camera_id"], c["version"]) for rm in revs for c in rm["cameras"]})
unique = {}
for tid in ids:
    try:
        records.resolve(revs, tid); unique[tid] = "one entry"
    except Exception as exc:
        unique[tid] = f"refused {exc}"
print(json.dumps({"tid1": r1.get("transform_id"), "tid2": r2.get("transform_id"),
                  "revisions": [[rm["version"], [records.transform_id(c["camera_id"], c["version"]) for c in rm["cameras"]]] for rm in revs],
                  "resolve_all": unique,
                  "newest_entries_for_den_oak": sum(c.get("camera_id") == "den-oak" for c in revs[-1]["cameras"]),
                  "work": str(w)}))
''')

p = subprocess.run([sys.executable, "-B", "-c", REGISTER, str(REG), str(work)], capture_output=True, text=True)
if p.returncode != 0:
    print("RESULT setup failed:\n" + p.stdout + p.stderr)
    sys.exit(3)
info = json.loads(p.stdout.strip().splitlines()[-1])
for version, cams in info["revisions"]:
    print(f"RESULT revision {version}: cameras {cams}")
q = subprocess.run([sys.executable, "-B", str(REG / "register.py"), "resolve", info["work"], info["tid1"]],
                   capture_output=True, text=True)
first = (q.stdout.strip().splitlines() or q.stderr.strip().splitlines() or [""])[0]
print(f"{'PASS' if q.returncode == 0 else 'FAIL'} den-register resolve of the OLD id {info['tid1']}: exit {q.returncode}: {first[:140]}")
ok = all(v == "one entry" for v in info["resolve_all"].values())
print(f"{'PASS' if ok else 'FAIL'} every transform_id in the versioned file resolves to exactly one entry: {info['resolve_all']}")
n = info["newest_entries_for_den_oak"]
print(f"{'PASS' if n == 1 else 'FAIL'} newest revision holds exactly one den-oak entry (rung 7 requires exactly one): {n}")
