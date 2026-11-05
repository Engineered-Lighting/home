"""Verifier probe: what den-verify does with the JSONL file correction-capture actually writes.

What it checks
  correction-capture (tools/correction-capture/capture.py) writes exposure.json
  (one JSON object, schema den-exposure-1, its per-action records under
  "corrections") and, with --events, corrections.jsonl whose lines are
  den-event-1 events {schema, t, seq, kind: "correction", source, data:
  {correction_id, entity_id, after, source}} (capture.py correction_events()).
  This feeds one such den-event-1 line, as written there, to den-verify's Run
  as a Correction, on the V11 synthetic evening, and reports whether den-verify
  ignores it silently or fails loudly.

Run
  python3 probe_verify-capture-jsonl-into-den-verify.py <den-verify branch checkout root>
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
T = syn.T0 + 36.0
event = {"schema": "den-event-1", "t": T, "seq": 0, "kind": "correction", "source": "correction_capture",
         "data": {"correction_id": "cc-%d" % round(T * 1000), "entity_id": "F1.spot",
                  "after": {"on": True, "brightness": 127, "kelvin": 3500}, "source": "switch_or_dimmer"}}
d, f, _ = syn.v11_evening()
try:
    Run(M, LightModel(syn.v11_model()), d, f, corrections=[event], clock=Clock(syn.UTC_OFFSET_S)).run()
    print("RESULT den-event-1 correction line: accepted without error (silent)")
except Exception as e:  # noqa: BLE001
    print(f"RESULT den-event-1 correction line: {type(e).__name__}: {e}")
