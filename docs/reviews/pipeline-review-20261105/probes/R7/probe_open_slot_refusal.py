#!/usr/bin/env python3
"""probe_open_slot_refusal: with METHOD-V1 as committed (meter_tolerance_lx, k_L_scope and meter_class
empty and marked open), do --score, --new-day and --capture refuse (exit 2, nothing written), while
--self-check still runs the round trip? And does a closed method with an empty tolerance refuse?

Runs the branch's photometry.py CLI as a subprocess with temp folders and a temp token; --camera points
at 127.0.0.1:9 (nothing listens; the refusal comes first). The closed-method checks use card.parse_method
on a temp copy of METHOD-V1 text.

    python3 -B probe_open_slot_refusal.py <branch-root>
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools/den-photometry/photometry.py"
sys.path.insert(0, str(ROOT / "tools/den-photometry"))
import card  # noqa: E402

bad = 0
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    root = tmp / "den-photometry"
    root.mkdir()
    day = root / "day-20261110"
    day.mkdir()
    token = tmp / "private" / "camera-token.txt"
    token.parent.mkdir()
    token.write_text("t" * 32)
    room = tmp / "room.json"
    room.write_text(json.dumps({"version": "den-v4", "surfaces": [], "cameras": []}))
    stage0 = tmp / "stage0" / "sweep-1"
    stage0.mkdir(parents=True)
    cases = {
        "score": ["--score", str(root), "--room-model", str(room), "--token-file", str(token)],
        "new-day": ["--new-day", str(root), "--stage0-sweep", str(stage0), "--token-file", str(token)],
        "capture": ["--capture", str(day), "--point", "desk", "--illuminant", "bulbs", "--blinds", "closed",
                    "--light", "four bulbs", "--camera", "http://127.0.0.1:9", "--token-file", str(token)],
    }
    before = sorted(str(p) for p in tmp.rglob("*"))
    for name, args in cases.items():
        p = subprocess.run([sys.executable, "-B", str(TOOL), *args], capture_output=True, text=True, timeout=120)
        out = (p.stdout + p.stderr).strip().splitlines()
        ok = p.returncode == 2 and any("still has lines the owner has not confirmed" in l for l in out) \
            and "meter_tolerance_lx" in "\n".join(out)
        bad += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} --{name} with open slots: exit {p.returncode}: {out[-1][:160] if out else ''}")
    after = sorted(str(p) for p in tmp.rglob("*"))
    print(f"{'PASS' if before == after else 'FAIL'} nothing written by the refused commands")
    bad += 0 if before == after else 1
    p = subprocess.run([sys.executable, "-B", str(TOOL), "--self-check"], capture_output=True, text=True, timeout=60)
    print(f"{'PASS' if p.returncode == 0 and 'ROUND TRIP PASS' in p.stdout else 'FAIL'} --self-check: exit {p.returncode}: "
          f"{p.stdout.strip().splitlines()[0][:150]}")

text = (ROOT / "tools/den-photometry/METHOD-V1.md").read_text(encoding="utf-8")
# closing every mark but leaving the tolerance empty must refuse
closed = re.sub(r"#\s*open[^\n]*", "", text)
try:
    card.parse_method(closed)
    print("FAIL closed marks with an empty meter_tolerance_lx parsed")
    bad += 1
except card.MethodError as exc:
    print(f"PASS closed marks with an empty tolerance refuse: {exc}")
filled = closed.replace("meter_tolerance_lx =", "meter_tolerance_lx = 10").replace("k_L_scope =", "k_L_scope = single") \
    .replace("meter_class =", "meter_class = class-b")
values, marked, _ = card.parse_method(filled)
m = card.Method(values, marked)
print(f"RESULT fully closed method: open={marked} ready_problems={card.ready_problems(m)}")
nan = filled.replace("meter_tolerance_lx = 10", "meter_tolerance_lx = NaN")
try:
    card.parse_method(nan)
    print("FAIL a NaN tolerance parsed")
    bad += 1
except card.MethodError:
    print("PASS a NaN tolerance refuses")
zero = card.Method({**values, "meter_tolerance_lx": 0}, ())
print(f"{'PASS' if card.ready_problems(zero) else 'FAIL'} a zero tolerance refuses: {card.ready_problems(zero)}")
bad += 0 if card.ready_problems(zero) else 1
print("RESULT", "FAIL" if bad else "PASS")
sys.exit(1 if bad else 0)
