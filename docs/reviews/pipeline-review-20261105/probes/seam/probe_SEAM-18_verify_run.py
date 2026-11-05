#!/usr/bin/env python3
"""Verifier probe for SEAM-18: what does den-verify's full scoring path (score.Run, as verify.py uses it)
do with rung 8 Stage 1's light-model.json? Crash, or record each decision as not verified?

    python3 -B probe_SEAM-18_verify_run.py <rung-10-verify checkout> <Stage 1 light-model.json>

Synthetic only: den-verify's own hue_decision/light_state fixtures (one decision on light.den_front_left,
then the same decision keyed by Stage 1's channel name), scored with the method's proposed values
(preview), against the Stage 1 file. No network, no files written. Prints RESULT lines.
"""
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
root, lm_path = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(root / "tools" / "den-verify"))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock  # noqa: E402
from score import Run  # noqa: E402

lm = json.loads(lm_path.read_text(encoding="utf-8"))
values = method.load(None).preview() if hasattr(method, "load") else None
t = syn.T0
for channel in ("light.den_front_left", "den_front_left.white"):
    ref = syn.light_state(t, {"r00c00": 0.01}, anchored=False, ident="ls-ref")
    send = [{"kind": "send", "sent_at": t + 0.6, "fade_s": 2.0}]
    dec = syn.hue_decision("d-" + channel, t + 0.5, {channel: 0.1}, {channel: {"level": 0.5, "steps": send}},
                           "ls-ref", model=lm["version"])
    frames = [ref] + [syn.light_state(t + o, {"r00c00": 0.3}, anchored=False) for o in (1.0, 3.0, 4.5)]
    try:
        run = Run(values, LightModel(lm), [dec], frames, clock=Clock(syn.UTC_OFFSET_S), scored=False)
        decisions, states, report = run.run()
        print("RESULT channel=%s: no crash; decisions verified %s of %s; hit rate %s verdict %s" % (
            channel, report["decisions"]["verified"], report["decisions"]["total"], report["hit_rate"]["value"],
            report["hit_rate"]["verdict"]))
        print("   verify_status:", decisions[0].get("verify_status"))
        print("   frame residual_status:", sorted({s.get("residual_status") for s in states if s.get("residual_status")}))
    except Exception as exc:  # a crash is the claim under test
        print("RESULT channel=%s: CRASH %s: %s" % (channel, type(exc).__name__, exc))
