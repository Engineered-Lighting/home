#!/usr/bin/env python3
"""Verifier for mutant-model-open-slot-refusal-survives (R8s1, reproduce lens).

    python3 -B verify_mutant-model-open-slot-refusal-survives.py <branch-checkout-root>

Checks the side claims of the finding at the pinned commit, with METHOD-V1 as committed (open slots):
  1. model.run_session(...) with the default scored=True refuses while a slot is open (the owner path).
  2. model.run_session(..., scored=False) gets past the open-slot refusal (it then stops at the next rule,
     the session outcome, because the folder holds no session).
  3. model.fit_and_score(...) writes light-model.json and score.json, verdict included, while slots are open.
  4. Which callers pass scored=False or call fit_and_score (grep of the tool's own files).
Synthetic den only, temp folders only, nothing contacts any host. The mutant itself (refusal removed,
full test_stage1 + test_den_light) is run by verify_mutant_survivors.py.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools" / "den-light"
sys.path.insert(0, str(TOOL))
import test_stage1 as T  # noqa: E402  (the suite's own helpers: synthetic den, fixtures)
import model  # noqa: E402
import synthetic  # noqa: E402


def main():
    committed = model.load_stage1()
    print(f"RESULT committed METHOD-V1 open slots: {len(committed.open)}")
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "session.json").write_text(json.dumps({"outcome": "aborted"}))
        try:
            model.run_session(folder, {"parameters": {}, "parameters_sha256": "0" * 64}, committed)
            print("FAIL 1: run_session(scored=True) did not refuse")
        except model.Refused as exc:
            print(f"PASS 1: run_session default refuses: {str(exc)[:90]}...")
        try:
            model.run_session(folder, {"parameters": {}, "parameters_sha256": "0" * 64}, committed, scored=False)
        except model.Refused as exc:
            open_refusal = "slot closed" in str(exc)
            print(f"RESULT 2: run_session(scored=False) refusal: {exc!s:.90} -> "
                  f"{'open-slot refusal' if open_refusal else 'past the open-slot refusal'}")
    m = committed.closed(block_px=40, reduce_pixel_step=10)  # keep it small; every other slot stays open
    assert m.open, "slots must still be open"
    den = synthetic.SyntheticDen(block_px=40, noise=1e-4)
    readings = T.readings_for(den, m)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "readings.json").write_text(json.dumps(readings))
        lm, scored = model.fit_and_score(folder, readings, T.FIXTURES, m, "1" * 64)
        written = sorted(p.name for p in folder.iterdir())
        print(f"RESULT 3: fit_and_score with {len(m.open)} open slots wrote {written}; "
              f"score verdict={scored['error']['verdict']}, light-model verdict={lm['error']['verdict']}")
        if "score.json" in written and "light-model.json" in written:
            print("CONFIRMED 3: fit_and_score writes scored files while slots are open (no refusal of its own)")
    callers = []
    for path in sorted(TOOL.glob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"scored=False|fit_and_score\(|run_session\(", line):
                callers.append(f"{path.name}:{n}: {line.strip()[:100]}")
    print("RESULT 4: callers:\n  " + "\n  ".join(callers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
