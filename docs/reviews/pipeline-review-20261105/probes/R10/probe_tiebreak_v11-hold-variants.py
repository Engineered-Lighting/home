"""Tiebreak probe for v11-hold-lost-with-capture-corrections: which Correction shapes give V11's frame a drift flag.

What it checks
  Case V11 (synthetic, den-verify's own synthetic.v11_evening, built from
  schemas.json's walkthrough) is run with four versions of A's dim c-190405:
    1. canon:      schemas.json's example Correction (hold.pinned.level_pct 50);
    2. ha_pin:     the same canon record, but hold.pinned is HA-shaped
                   {on, brightness 127, kelvin 3500} (no level_pct / cct_k);
    3. hold_null:  the same canon record with hold: null;
    4. no_hold:    the same canon record with the hold key removed.
  For each it prints the counter-1 residual entry on ls-190409 (predicted,
  residual_pct, flags) and whether the drift flag appears.
  It shows whether an HA-shaped pin leads to drift (the finding's second claim)
  or only to a wrong prediction that is still flagged held.

Run
  python3 -B probe_tiebreak_v11-hold-variants.py <den-verify branch checkout root>
Synthetic data only; no network; writes nothing.
"""
import copy
import sys

sys.dont_write_bytecode = True
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-verify"))

import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

M = method.parse(method.closed_copy(method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md"))).scored()


def ls190409(corrections):
    d, f, _ = syn.v11_evening()
    decs, states, rep = Run(M, LightModel(syn.v11_model()), d, f, corrections=corrections,
                            clock=Clock(syn.UTC_OFFSET_S)).run()
    s = next(s for s in states if s["ts"] == syn.T0 + 40.0)
    return next(e for e in s["residual"] if e["surface_id"] == "counter-1"), rep


_, _, (canon,) = syn.v11_evening()
ha = copy.deepcopy(canon)
ha["hold"]["pinned"] = {"on": True, "brightness": 127, "kelvin": 3500}
null = copy.deepcopy(canon)
null["hold"] = None
gone = copy.deepcopy(canon)
gone.pop("hold")

for name, c in (("canon", canon), ("ha_pin", ha), ("hold_null", null), ("no_hold", gone)):
    e, rep = ls190409([c])
    print(f"RESULT {name:9s}: predicted={e['predicted']} residual_pct={e['residual_pct']} flags={e['flags']} "
          f"drift={'drift' in e['flags']} hit_rate={rep['hit_rate']['hits']}/{rep['hit_rate']['hits'] + rep['hit_rate']['misses']}")
