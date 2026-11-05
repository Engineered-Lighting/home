"""Verifier probe (reproduce lens) for finding pass-on-thin-evidence.

What it checks
  score.py Run.hit_rate() (line 503-530): the verdict is Fraction(hits, n) >= 0.85
  for any n >= 1; the session-bootstrap interval and delta_hit_rate are
  reported but never enter the verdict.
    1. hit_rate over {day1: (1, 1)}: verdict, interval;
    2. {day1: (20, 20), day2: (14, 20)}: verdict and interval;
    3. the same with delta_hit_rate 0.05 vs 0.30 (both closed): verdict and the
       report's delta_status text;
    4. end to end through verify.py run (scored, all slots closed): one decision,
       one point that lands exactly -> the console line and exit code.

Run
  python3 verify_pass-on-thin-evidence.py <den-verify branch checkout root>
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
from score import Run  # noqa: E402

TEXT = method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md")


def hr(days, **over):
    m = method.parse(method.closed_copy(TEXT, over)).scored()
    return Run.hit_rate(type("R", (), {"m": m})(), days)


a = hr({1: (1, 1)})
print(f"RESULT 1 of 1, one session: verdict={a['verdict']} interval={a['interval']}")
b = hr({1: (20, 20), 2: (14, 20)})
print(f"RESULT 20/20 + 14/20: value={b['value']} verdict={b['verdict']} interval={b['interval']}")
for dl in (0.05, 0.30):
    c = hr({1: (20, 20), 2: (14, 20)}, delta_hit_rate=dl)
    print(f"RESULT delta_hit_rate={dl} (closed): verdict={c['verdict']} delta={c['delta']} "
          f"delta_status={c['delta_status']!r}")

B0 = syn.HUE[0]
MODEL = syn.hue_model({"desk-1": {B0: 400.0}})
LM = LightModel(MODEL)
t = syn.T0
st = lambda lv: {B0: {"level": lv, "cct_k": 2700}}  # noqa: E731
ref = syn.light_state(t, {"desk-1": LM.predict(st(0.2))["desk-1"]}, anchored=False, ident="ls-ref")
after = syn.light_state(t + 5, {"desk-1": LM.predict(st(0.8))["desk-1"]}, anchored=False)
dec = syn.hue_decision("d", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.6}]}},
                       "ls-ref", targets={"A": {"surface_id": "desk-1"}})
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / "d.jsonl").write_text(json.dumps(dec) + "\n", encoding="utf-8")
    (tmp / "l.jsonl").write_text(json.dumps(ref) + "\n" + json.dumps(after) + "\n", encoding="utf-8")
    (tmp / "m.json").write_text(json.dumps(MODEL), encoding="utf-8")
    (tmp / "V.md").write_text(method.closed_copy(TEXT), encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(["--method", str(tmp / "V.md"), "run", "--decisions", str(tmp / "d.jsonl"),
                         "--light-states", str(tmp / "l.jsonl"), "--model", str(tmp / "m.json"),
                         "--utc-offset-h", "-7", "--out", str(tmp / "o")])
    print(f"RESULT CLI one point: exit {code}: {buf.getvalue().strip().splitlines()[0]}")
