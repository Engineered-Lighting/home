"""Verifier probe (reproduce lens) for finding held-hits-kept-misses-dropped.

What it checks
  Independently of the attack probe, through den-verify's own command line
  (verify.py run, scored, on a closed temp copy of VERIFY-V1.md): one Decision
  whose held bulb gives >= held_share_min (proposed 0.05) of every point's
  predicted light, with 2 of 10 points within tolerance and 8 points 40% low.
  If held exclusion were symmetric the decision would score 0 of 0 (or 2 of 10
  if held were ignored); the tool prints its console verdict line.
  A control run with the same frames and the bulb not held is also printed.

Run
  python3 -B verify_held-hits-kept-misses-dropped.py <den-verify checkout root>
Synthetic data only; temp dirs only; no network; writes nothing in the checkout.
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

B0, B1 = syn.HUE[:2]
PTS = [f"q{i}" for i in range(10)]
MODEL = syn.hue_model({p: {B0: 300.0, B1: 200.0} for p in PTS})
LM = LightModel(MODEL)


def case(held):
    t = syn.T0
    before = {B0: {"level": 0.3, "cct_k": 2700}, B1: {"level": 0.4, "cct_k": 2700}}
    after = {B0: {"level": 0.9, "cct_k": 2700}, B1: {"level": 0.4, "cct_k": 2700}}
    pb, pa = LM.predict(before), LM.predict(after)
    ref = syn.light_state(t, {p: pb[p] for p in PTS}, anchored=False, ident="ls-r")
    applied = {B0: {"level": 0.9, "steps": [{"kind": "send", "sent_at": t + 0.6}]},
               B1: {"status": "held" if held else "sent", "level": 0.4}}
    hs = {"revision": 3, "held": [{"entity_id": B1, "channels": [B1],
                                   "pinned": {"level_pct": 40, "cct_k": 2700, "source": "read-back"}}]} if held else None
    dec = syn.hue_decision("d-1", t + 0.5, {B0: 0.3, B1: 0.4}, applied, "ls-r",
                           targets={f"T{i}": {"surface_id": p} for i, p in enumerate(PTS)}, hold_set=hs)
    vals = {p: (pa[p] if i < 2 else 0.6 * pa[p]) for i, p in enumerate(PTS)}
    aft = syn.light_state(t + 6, vals, anchored=False, ident="ls-a")
    share = LM.predict({B1: after[B1]})[PTS[0]] / pa[PTS[0]]
    return [dec], [ref, aft], share


for held in (True, False):
    decs, frames, share = case(held)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "d.jsonl").write_text("".join(json.dumps(d) + "\n" for d in decs), encoding="utf-8")
        (tmp / "l.jsonl").write_text("".join(json.dumps(f) + "\n" for f in frames), encoding="utf-8")
        (tmp / "m.json").write_text(json.dumps(MODEL), encoding="utf-8")
        (tmp / "V.md").write_text(method.closed_copy(method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md")),
                                  encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--method", str(tmp / "V.md"), "run", "--decisions", str(tmp / "d.jsonl"),
                             "--light-states", str(tmp / "l.jsonl"), "--model", str(tmp / "m.json"),
                             "--utc-offset-h", "-7", "--out", str(tmp / "o")])
        rep = json.loads((tmp / "o" / "report.json").read_text("utf-8"))
        print(f"RESULT held={held} held_share={share:.3f} exit={code} console: {buf.getvalue().splitlines()[0]}")
        print(f"RESULT held={held} report hits={rep['hit_rate']['hits']} misses={rep['hit_rate']['misses']} "
              f"excluded_held={rep['excluded_held_or_superseded']} verdict={rep['hit_rate']['verdict']}")
