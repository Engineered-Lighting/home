"""Probe: case V11 with a Correction in the shape correction-capture writes: the hold vanishes, ls-190409 reads as drift.

What it checks
  den-verify takes a person's hold only from a schema-shaped top-level
  `Correction.hold` {entity_id, channels, pinned {level_pct, cct_k}, since,
  hold_set_revision} (score.py _add_correction: `h = c.get("hold")`; nothing
  happens when it is missing, and no warning is printed). The only Correction
  writer in the repo today, correction-capture (capture.py, corrections.jsonl),
  writes no top-level `hold`: each change carries `hold: "opened"|"replaced"|
  "confirmed"` and `after: {on, brightness, kelvin}`. den-core-live's
  incumbent pins holds as {on, brightness, kelvin} too (no level_pct).

  V11 (synthetic, from schemas.json's walkthrough) is run twice:
    1. with schemas.json's example Correction c-190405 (hold pins F1.spot at 50%):
       ls-190409 is predicted 368 lx, residual -2.99%, flagged held, no drift;
    2. with the same change written as correction-capture writes it (same time,
       same settle window, F1.spot after = brightness 127 of 254):
       the hold is silently dropped, ls-190409 is predicted 510 lx, residual
       -30%, flagged outside + drift - the outcome the ladder forbids.

Run
  python3 probe_v11-hold-lost-with-capture-corrections.py <den-verify branch checkout root>
Synthetic data only; no network; writes nothing.
"""
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
T_CHANGE = syn.T0 + 36.0  # 19:04:05 local, as c-190405


def capture_shaped():
    """One action as correction-capture's capture.py writes it (lines 476-485)."""
    return {"correction_id": "cc-%d" % round(T_CHANGE * 1000), "ts": T_CHANGE, "ts_utc": "synthetic",
            "source": "switch_or_dimmer", "people": True, "mode_stratum": "no_game", "session_id": "x/no_game",
            "ha_context": {"id": None, "user_id": None, "parent_id": None},
            "action": {"grouping": "one change", "settle_s": 3.0, "n_changes": 1},
            "changes": [{"entity_id": "F1.spot", "t": T_CHANGE, "hold": "opened", "source": "switch_or_dimmer",
                         "ha_context": {"id": None, "user_id": None, "parent_id": None},
                         "before": {"on": True, "brightness": 178, "kelvin": 3500}, "before_from": "observed",
                         "after": {"on": True, "brightness": 127, "kelvin": 3500}}],
            "attribution": None}


def ls190409(corrections):
    d, f, _ = syn.v11_evening()
    _, states, rep = Run(M, LightModel(syn.v11_model()), d, f, corrections=corrections,
                         clock=Clock(syn.UTC_OFFSET_S)).run()
    s = next(s for s in states if s["ts"] == syn.T0 + 40.0)
    return next(e for e in s["residual"] if e["surface_id"] == "counter-1")


_, _, (example,) = syn.v11_evening()
a = ls190409([example])
b = ls190409([capture_shaped()])
print(f"RESULT schema-shaped c-190405: predicted={a['predicted']} residual_pct={a['residual_pct']} flags={a['flags']}")
print(f"RESULT capture-shaped change:  predicted={b['predicted']} residual_pct={b['residual_pct']} flags={b['flags']}")
bad = "drift" not in a["flags"] and "drift" in b["flags"] and b["predicted"] != a["predicted"]
print("FAIL: with the repo's own Correction records the hold is dropped silently and V11's frame is flagged drift"
      if bad else "PASS")
