#!/usr/bin/env python3
"""probe_method_edit_after_session: model.py scores a session under a METHOD-V1 Stage 1 block that was
edited after the probe session, without refusing or flagging it, so the scoring rule can be tuned on the
session's own hold-outs (here the gate numbers, but any line works: noise floor, bracket tolerance,
minimum share, signal-rich fraction).

What it checks: probe.py writes method_stage1_block_sha256 into session.json; model.run_session() never
compares it with the block it loads. The probe builds a session whose model error sits just above the
gate under the committed block, then scores it once with the committed block and once with an edited
copy of METHOD-V1.md (gate_median_percent 8 -> 12, gate_p90_percent 19 -> 30).

How to run (synthetic captures in memory, temp dirs only, no network):
    python3 -B probe_method_edit_after_session.py <root of the claude/rung-8-stage1 checkout>
"""
import json
import math
import re
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import model  # noqa: E402
import synthetic  # noqa: E402
import test_stage1 as T  # noqa: E402

FAST = dict(block_px=40, reduce_pixel_step=10)
SAT = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0


def captures_for(den, m):
    owner = model.pixel_blocks(m)
    plan = model.schedule(m, T.SNAPSHOT, T.RANGES, T.WARMEST)
    out, t = [], 0.0
    for step in plan["steps"]:
        t += 2.5
        blocks = den.reading(step["scene"], t)
        frames = []
        for j in range(2):  # two frames that differ a little, so every block has an all-off noise
            values = [blocks[b] * (1 + 1e-4 * math.sin(7 * p + j)) for p, b in enumerate(owner)]
            frames.append((values, [False] * len(owner)))
        out.append({"step": step, "t": t, "frames": frames})
    return out, plan["temperatures"]


def score_with(folder, m, captures, session_block):
    folder.mkdir(parents=True)
    (folder / "session.json").write_text(json.dumps({
        "outcome": "complete", "id": "s1", "temperatures": captures[1],
        "method_stage1_block_sha256": session_block, "stage0": {"meter_norm_sha256": "x" * 64}}))
    (folder / "fixtures.jsonl").write_text("".join(json.dumps(f) + "\n" for f in T.FIXTURES))
    with mock.patch.object(model, "session_captures", lambda f, p, m1: captures[0]):
        lm, scored = model.run_session(folder, {"parameters": {}, "parameters_sha256": "x" * 64}, m)
    return lm, scored


def main():
    text = (ROOT / "tools" / "den-light" / "METHOD-V1.md").read_text(encoding="utf-8")
    committed = model.parse_stage1(text).proposed().closed(**FAST)
    edited_text = re.sub(r"(?m)^gate_median_percent = 8\b", "gate_median_percent = 12", text)
    edited_text = re.sub(r"(?m)^gate_p90_percent = 19\b", "gate_p90_percent = 30", edited_text)
    edited = model.parse_stage1(edited_text).proposed().closed(**FAST)
    print("RESULT committed block sha:", committed.block_sha256[:12], "edited block sha:", edited.block_sha256[:12])
    # a den whose sum saturates a little: the superposition model is off by roughly 10% on bright scenes
    den = synthetic.SyntheticDen(block_px=40, noise=0.0, drift=0.0, saturate=SAT)
    captures = captures_for(den, committed)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        _, a = score_with(tmp / "a", committed, captures, committed.block_sha256)
        print("RESULT committed block:", {k: a["error"][k] for k in ("median", "p90", "verdict")})
        try:
            _, b = score_with(tmp / "b", edited, captures, committed.block_sha256)
        except model.Refused as exc:
            print("PASS: refused a block that differs from the session's:", exc)
            return
        session_sha = json.loads((tmp / "b" / "session.json").read_text())["method_stage1_block_sha256"]
        score_sha = json.loads((tmp / "b" / "score.json").read_text())["method_stage1_block_sha256"]
        print("RESULT edited block, same session:", {k: b["error"][k] for k in ("median", "p90", "verdict")})
        print("RESULT session.json block sha", session_sha[:12], "!= score.json block sha", score_sha[:12],
              ":", session_sha != score_sha)
        if a["error"]["verdict"] != b["error"]["verdict"]:
            print("FAIL: editing the Stage 1 block after the session flips the verdict from",
                  a["error"]["verdict"], "to", b["error"]["verdict"], "and model.py neither refuses nor warns")
        else:
            print("FAIL (weaker): model.py scored under a block that differs from the session's without refusing")


if __name__ == "__main__":
    main()
