"""Probe (attack lens): the ladder's fixed numbers in VERIFY-V1.md can be changed and a scored run still prints "pass".

What it checks
  The owner must edit VERIFY-V1.md (or pass --method) to close the 12 open
  slots before any scored run. Nothing pins the numbers the ladder and owner
  choices fix (tolerance 0.15, hit_rate_min 0.85, interval_level 0.95,
  bootstrap_rounds 10000, v_min 0.5, tau_r_cm 5): method.py only checks that a
  value is a finite number, and the report records the file's sha256 without
  comparing it with anything. The same edit that closes the slots can widen
  the tolerance; the console line names only the hit-rate gate.

  1. Default VERIFY-V1.md: scored run refused (exit 2) - the open-slot guard works.
  2. Closed copy: a decision whose two points land 30% and 40% off -> fail.
  3. Closed copy with tolerance = 0.5 (and hit_rate_min = 0.85 untouched):
     same records -> "pass", exit 0, nothing flags the changed tolerance.

Run
  python3 probe_atk-fixed-gate-numbers-editable.py <den-verify branch checkout root>
Synthetic data only; no network; temp dirs only.
"""
import contextlib
import io
import json
import sys
import tempfile

sys.dont_write_bytecode = True
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-verify"))

import method  # noqa: E402
import synthetic as syn  # noqa: E402
import verify as cli  # noqa: E402
from light_model import LightModel  # noqa: E402

B0 = syn.HUE[0]
TEXT = method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md")
MODEL = syn.hue_model({"desk-1": {B0: 400.0}, "sofa-1": {B0: 300.0}})
LM = LightModel(MODEL)
t = syn.T0
st = lambda lv: {B0: {"level": lv, "cct_k": 2700}}  # noqa: E731
pb, pa = LM.predict(st(0.2)), LM.predict(st(0.8))
ref = syn.light_state(t, {k: pb[k] for k in ("desk-1", "sofa-1")}, anchored=False, ident="ls-ref")
after = syn.light_state(t + 5, {"desk-1": pa["desk-1"] * 1.3, "sofa-1": pa["sofa-1"] * 0.6}, anchored=False)
dec = syn.hue_decision("d", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.6}]}},
                       "ls-ref", targets={"A": {"surface_id": "desk-1"}, "B": {"surface_id": "sofa-1"}})


def run(method_text):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "d.jsonl").write_text(json.dumps(dec) + "\n", encoding="utf-8")
        (tmp / "l.jsonl").write_text(json.dumps(ref) + "\n" + json.dumps(after) + "\n", encoding="utf-8")
        (tmp / "m.json").write_text(json.dumps(MODEL), encoding="utf-8")
        (tmp / "V.md").write_text(method_text, encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--method", str(tmp / "V.md"), "run", "--decisions", str(tmp / "d.jsonl"),
                             "--light-states", str(tmp / "l.jsonl"), "--model", str(tmp / "m.json"),
                             "--utc-offset-h", "-7", "--out", str(tmp / "o")])
        return code, buf.getvalue().strip().splitlines()[0]


c1 = run(TEXT)
c2 = run(method.closed_copy(TEXT))
c3 = run(method.closed_copy(TEXT, {"tolerance": 0.5}))
print(f"RESULT default method file: exit {c1[0]}: {c1[1][:90]}")
print(f"RESULT closed copy:         exit {c2[0]}: {c2[1]}")
print(f"RESULT tolerance = 0.5:     exit {c3[0]}: {c3[1]}")
bad = c1[0] == 2 and "fail" in c2[1] and c3[0] == 0 and "pass" in c3[1]
print("FAIL: a scored run accepts a changed ladder tolerance and reports pass without flagging it" if bad else "PASS")
