#!/usr/bin/env python3
"""verify_intent_model_refuses_open: the committed METHOD-V1 Stage 1 block has open slots, and
model.run_session refuses a scored run with it before reading the folder (the refusal works today).

What it checks (verifier for finding model-open-slot-refusal-untested): run_session(folder, ..., load_stage1())
on an empty temp folder raises model.Refused naming the open slots, and writes nothing. The mutant run
(removing method1.refuse_if_open in run_session, full test_stage1 module) is recorded separately in
scratchpad/mut/R8s1-verify-intent/mutant_refusal_full_test_stage1.log.

How to run (temp dir only, no network):
    python3 -B verify_intent_model_refuses_open.py <root of the claude/rung-8-stage1 checkout>
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import model  # noqa: E402


def main():
    m = model.load_stage1()
    print("RESULT open slots in the committed block:", len(m.open))
    with tempfile.TemporaryDirectory() as tmp:
        try:
            model.run_session(Path(tmp), {"parameters": {}, "parameters_sha256": "0" * 64}, m)
            print("FAIL: run_session did not refuse")
        except model.Refused as exc:
            print("RESULT Refused:", str(exc)[:120], "...")
            print("RESULT files written:", sorted(p.name for p in Path(tmp).iterdir()))
            print("PASS: the refusal works today")


if __name__ == "__main__":
    main()
