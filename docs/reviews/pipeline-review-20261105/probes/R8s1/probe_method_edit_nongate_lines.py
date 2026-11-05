#!/usr/bin/env python3
"""probe_method_edit_nongate_lines: verifier's follow-up to probe_method_edit_after_session.py
(finding stage1-block-edit-after-session-not-refused).

What it checks: whether editing a Stage 1 line other than the gate numbers (noise_floor_multiple,
signal_rich_fraction, shared_curve_percent) after a session changes the verdict model.py prints for the
same synthetic session, and whether model.py refuses any of them. Reuses the reporter's probe helpers
(a slightly saturating synthetic den, captures in memory).

Synthetic captures in memory, temp dirs only, no network.

How to run:
    python3 -B probe_method_edit_nongate_lines.py <root of the claude/rung-8-stage1 checkout>
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.argv = [sys.argv[0], str(ROOT)]
import probe_method_edit_after_session as P  # noqa: E402

model, synthetic = P.model, P.synthetic


def main():
    text = (ROOT / "tools" / "den-light" / "METHOD-V1.md").read_text(encoding="utf-8")
    base = model.parse_stage1(text).proposed().closed(**P.FAST)
    den = synthetic.SyntheticDen(block_px=40, noise=0.0, drift=0.0, saturate=P.SAT)
    captures = P.captures_for(den, base)
    edits = [("noise_floor_multiple", "5", "200"), ("noise_floor_multiple", "5", "2000"),
             ("signal_rich_fraction", "0.2", "0.6"), ("shared_curve_percent", "3", "30")]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        _, a = P.score_with(tmp / "base", base, captures, base.block_sha256)
        print("RESULT committed:", {k: a["error"][k] for k in ("median", "p90", "count", "verdict")})
        for i, (name, old, new) in enumerate(edits):
            edited_text = re.sub(rf"(?m)^{name} = {re.escape(old)}\b", f"{name} = {new}", text)
            m = model.parse_stage1(edited_text).proposed().closed(**P.FAST)
            try:
                _, b = P.score_with(tmp / f"e{i}", m, captures, base.block_sha256)
            except model.Refused as exc:
                print(f"RESULT {name} {old}->{new}: refused ({exc})")
                continue
            print(f"RESULT {name} {old}->{new}:", {k: b["error"][k] for k in ("median", "p90", "count", "verdict")},
                  "refused: no")


if __name__ == "__main__":
    main()
