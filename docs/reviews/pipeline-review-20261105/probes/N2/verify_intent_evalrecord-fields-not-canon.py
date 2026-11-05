#!/usr/bin/env python3
"""Verifier probe (intent/spec lens) for finding evalrecord-fields-not-canon.

What it checks, on synthetic data only (the checkout's own test World, in a temp dir):
  1. Which canon EvalRecord fields (docs/architecture/live-v1/schemas.json) the held-out
     report carries, and which N2-specific canon fields (gold_set incl. n1, verdict) it carries.
  2. Whether `decision` is absent (schemas.json says N2 writes `verdict` instead of `decision`).
  3. Plan 4.8's `variant` item "served model": is any model / artifact identity in the report,
     although every synthetic receipt carries an artifact_sha256 (as qwen-observation-1 does)?
  4. Plan 1(b)/11: with GATE-V1 prompt = a prompt-2, does the report say anywhere that the
     prompt was written on development with the staged cases in view?

Run:
    python3 -I verify_intent_evalrecord-fields-not-canon.py <den-n2 checkout root>
No network, no camera, no real data; everything lives in a TemporaryDirectory.
"""
import json
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-n2"))
sys.path.insert(0, str(root / "tools" / "den-label"))
import test_den_n2 as t  # noqa: E402  the checkout's own synthetic World
import score  # noqa: E402

schemas = json.loads((root / "docs/architecture/live-v1/schemas.json").read_text(encoding="utf-8"))
eval_rec = next(r for r in schemas["records"] if r["id"] == "eval_record")
canon_top = []
for f in eval_rec["fields"]:
    for name in f["name"].split(" / "):
        if "." not in name and "[" not in name:
            canon_top.append(name.strip())
print("RESULT canon EvalRecord top-level names:", canon_top)


def add_artifact(world, version=None):
    for path in (world.n2 / "asks").glob("*.jsonl"):
        out = []
        for line in path.read_text(encoding="ascii").splitlines():
            d = json.loads(line)
            if d.get("receipt"):
                d["receipt"]["artifact_sha256"] = "9" * 64
                if version:
                    d["receipt"]["prompt_version"] = version
            out.append(d)
        world.write_jsonl(path, out)


with tempfile.TemporaryDirectory(prefix="n2-verify-") as tmp:
    world = t.World(tmp, t.FOUR_DAYS)
    add_artifact(world)
    report, path = world.run("held_out")
    keys = sorted(report)
    print("RESULT report keys:", keys)
    print("RESULT verdict:", report["verdict"], "schema:", report["schema"])
    present = [k for k in canon_top if k in report]
    absent = [k for k in canon_top if k not in report]
    print("RESULT canon top-level present:", present)
    print("RESULT canon top-level absent:", absent)
    gold_keys = sorted(report["gold_set"])
    print("RESULT gold_set keys:", gold_keys)
    print("PASS" if "n1" in report["gold_set"] and "verdict" in report else "FAIL",
          "N2-specific canon blocks (gold_set.n1, verdict) present")
    print("PASS" if "decision" not in report else "FAIL", "decision absent, as schemas.json says for N2")
    print("RESULT replay field shape (canon: {determinism, holds_pinned, pacing}):",
          type(report["replay"]).__name__, sorted(report["replay"][0]) if report["replay"] else None)
    text = json.dumps(report)
    print("RESULT variant keys:", sorted(report["variant"]))
    has_model = any(k in report["variant"] for k in ("model", "served_model", "artifact_sha256", "artifacts"))
    print(("FAIL" if not has_model else "PASS"), "plan 4.8 'served model' in variant",
          "(artifact sha in report text:", "9" * 64 in text, ")")

with tempfile.TemporaryDirectory(prefix="n2-verify-p2-") as tmp:
    world = t.World(tmp, t.FOUR_DAYS)
    p2 = "n2-scene-prompt-2"
    add_artifact(world, version=p2)
    world.gate.write_text(t.gate_text(t.filled(prompt={"version": p2, "sha256": "f" * 64})), encoding="utf-8")
    report, path = world.run("held_out")
    print("RESULT prompt-2 run verdict:", report["verdict"], "variant.prompt:", report["variant"]["prompt"])
    print("RESULT notes:", report["notes"])
    blob = json.dumps(report).lower()
    said = any(w in blob for w in ("staged cases", "written on development", "written with", "in view"))
    print(("PASS" if said else "FAIL"), "report says the prompt-2 was written on development with the staged cases in view")
    out = []
    score_lines = score.summary(report, path)
    print("RESULT summary mentions prompt:", any("prompt" in line.lower() for line in score_lines))
