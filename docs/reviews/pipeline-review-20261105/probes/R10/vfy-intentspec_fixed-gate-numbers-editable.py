"""Verifier probe: when the owner widens the tolerance in the method file, is it really invisible?

What it checks
  The finding fixed-gate-numbers-editable says a scored run with tolerance 0.5
  reports pass "with nothing flagged". This probe runs the same synthetic case
  through verify.py (scored, closed copy of VERIFY-V1.md with tolerance 0.5,
  and once with the committed 0.15), then reads what the run wrote:
    - Decision.verify.tolerance in decisions.verified.jsonl (a schemas.json field),
    - report.json method.values.tolerance and method.sha256,
    - whether the sha256 differs from the committed file's.
  It also prints the committed file's fixed values that test_verify.py pins.

Run
  python3 vfy-intentspec_fixed-gate-numbers-editable.py <den-verify branch checkout root>
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
COMMITTED = method.load(ROOT / "tools/den-verify/VERIFY-V1.md")
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
        verified = json.loads((tmp / "o" / "decisions.verified.jsonl").read_text(encoding="utf-8").splitlines()[0])
        report = json.loads((tmp / "o" / "report.json").read_text(encoding="utf-8"))
        return code, buf.getvalue().strip().splitlines()[0], verified["verify"]["tolerance"], \
            report["method"]["values"]["tolerance"], report["method"]["sha256"]


print(f"RESULT committed fixed values: tolerance {COMMITTED.values['tolerance']}, hit_rate_min "
      f"{COMMITTED.values['hit_rate_min']}, v_min {COMMITTED.values['v_min']}, interval_level "
      f"{COMMITTED.values['interval_level']}, bootstrap_rounds {COMMITTED.values['bootstrap_rounds']}; "
      f"sha256 {COMMITTED.sha256[:12]}")
for label, text in (("closed, 0.15", method.closed_copy(TEXT)), ("closed, 0.5", method.closed_copy(TEXT, {"tolerance": 0.5}))):
    code, line, vtol, rtol, sha = run(text)
    print(f"RESULT {label}: exit {code}: {line}")
    print(f"RESULT {label}: Decision.verify.tolerance={vtol} report.method.values.tolerance={rtol} "
          f"report.method.sha256={sha[:12]} (differs from committed: {sha != COMMITTED.sha256})")
