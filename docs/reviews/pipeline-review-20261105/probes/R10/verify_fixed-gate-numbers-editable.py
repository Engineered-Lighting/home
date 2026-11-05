"""Verifier probe (reproduce lens) for finding fixed-gate-numbers-editable.

What it checks
  1. method.parse() accepts edits to the numbers VERIFY-V1.md lists under
     "Fixed by the contracts or the owner" (tolerance, hit_rate_min,
     interval_level, bootstrap_rounds, v_min, tau_r_cm), including absurd ones.
  2. Through the command line (verify.py run --method <closed copy>), one
     decision with two points 30% and 40% off: fail at tolerance 0.15, pass at
     tolerance 0.5, exit 0 both times; prints the console line.
  3. Where the widened tolerance IS recorded: report.json method.values and
     method.sha256, and Decision.verify.tolerance in decisions.verified.jsonl.

Run
  python3 verify_fixed-gate-numbers-editable.py <den-verify branch checkout root>
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

TEXT = method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md")

# 1. absurd edits to the "fixed" numbers are accepted
for over in ({"tolerance": 0.5}, {"hit_rate_min": 0.0}, {"interval_level": 1.5}, {"bootstrap_rounds": 0},
             {"v_min": 0.0}, {"tau_r_cm": 500}, {"tolerance": -1}):
    try:
        vals = method.parse(method.closed_copy(TEXT, over)).scored()
        k = next(iter(over))
        print(f"RESULT parse accepts {over}: scored value {k}={vals[k]}")
    except ValueError as e:
        print(f"RESULT parse refuses {over}: {e}")

# 2. and 3. end to end
B0 = syn.HUE[0]
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
        rep = json.loads((tmp / "o" / "report.json").read_text(encoding="utf-8"))
        d = json.loads((tmp / "o" / "decisions.verified.jsonl").read_text(encoding="utf-8").splitlines()[0])
        return code, buf.getvalue().strip().splitlines()[0], rep, d


for tol in (0.15, 0.5):
    code, line, rep, d = run(method.closed_copy(TEXT, {"tolerance": tol}))
    print(f"RESULT tolerance {tol}: exit {code}: {line}")
    print(f"    report.json method.values.tolerance={rep['method']['values']['tolerance']} "
          f"sha256={rep['method']['sha256'][:12]}...; Decision.verify.tolerance={d['verify']['tolerance']}; "
          f"tolerance in console line: {'tolerance' in line or str(tol) in line}")
