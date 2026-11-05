"""Tiebreak support for checks-not-independent: where does a wrong transform still pass a 5 cm check?

Fits the synthetic scene with p1 mis-measured by +10 cm in z (3 and 4 fit points) and
reports, per height, the share of visible 10 cm grid points where the accepted transform is
within 5 cm of the truth (where an honest check placed there would pass).
Synthetic data and temp folders only.
Run:  python3 -B tiebreak_error_field.py <rung-1 checkout root>
"""
import io, json, math, re, sys, tempfile
from pathlib import Path
root = Path(sys.argv[1]).resolve(); tool = root / "tools" / "den-register"; sys.path.insert(0, str(tool))
import method, register, rigid, synthetic
text = (tool / "REGISTER-V1.md").read_text(encoding="utf-8")
text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", r"\1 = \2  # closed", text, flags=re.M)
M = method.parse(text)
TR, TT = synthetic.camera_pose()
def fitmat(fit):
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t); P = synthetic.POINTS
        depth, left, seen = synthetic.render(P)
        take = synthetic.write_take(tmp / "take1", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
        work = tmp / "work"; work.mkdir()
        sheet = synthetic.worksheet(P, fit, ("c1", "c2"), {"p1": [0, 0, 0.10]})
        (work / "worksheet.json").write_text(json.dumps(sheet), encoding="utf-8")
        ws = register.worksheet_file.load(work / "worksheet.json", need_positions=False)
        s = register.ClickSession(work, take, register.frame_number(take, None, work), ws, M)
        for pid in list(fit) + ["c1", "c2"]:
            u, v, _ = seen[pid]; s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
        s.done()
        code, rep = register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
        return rep["matrix"]
for fit in (("p1","p2","p3"), ("p1","p2","p3","p4")):
    r, t = rigid.split4(fitmat(fit))
    for z in (0.0, 0.45, 0.75, 1.2):
        ok = tot = 0
        for i in range(1, 46):
            for j in range(1, 38):
                q = [i * 0.1, j * 0.1, z]
                cam = synthetic.to_camera(TR, TT, q)
                u, v = synthetic.project(cam)
                if cam[2] <= 0 or not (0 <= u < synthetic.W and 0 <= v < synthetic.H):
                    continue
                tot += 1
                ok += math.dist(rigid.apply(r, t, cam), q) <= 0.05
        print(f"RESULT fit {len(fit)} pts, height {z:.2f} m: transform within 5 cm of truth at {ok}/{tot} visible grid points ({100*ok/max(tot,1):.0f}%)")
