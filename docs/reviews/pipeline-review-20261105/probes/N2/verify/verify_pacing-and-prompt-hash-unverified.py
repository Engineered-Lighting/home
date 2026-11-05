"""Verifier probe for finding pacing-and-prompt-hash-unverified (N2, den-n2 score.py).

Builds den-n2's synthetic held-out World (asks every 4 s on each segment; temp folder; fake check.py) and scores
it under gates declaring pacing_s 4 (matching), 2 and 8 (not matching the ask lines), and under a gate whose
prompt.sha256 is a different well-formed digest. Prints: the ask lines' actual spacing, whether score.py
refused, the verdict, variant.pacing_s, the gated onset value and D, and variant.prompt.

Run:  python3 -B verify_pacing-and-prompt-hash-unverified.py <den-n2 checkout root>
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402
import score  # noqa: E402

for label, changes in (("pacing_s=4 (matches)", {"pacing_s": 4}), ("pacing_s=2", {"pacing_s": 2}),
                       ("pacing_s=8", {"pacing_s": 8}),
                       ("prompt.sha256 other digest", {"prompt": {"version": "lab-live-scene-prompt-1",
                                                                  "sha256": "9" * 64}})):
    with tempfile.TemporaryDirectory(prefix="n2-verify-pacing-") as tmp:
        world = T.World(Path(tmp), T.FOUR_DAYS)
        take = world.held_out()[0]
        rs = [json.loads(x)["r"] for x in (world.n2 / "asks" / f"{take}.jsonl").read_text().splitlines()]
        spacing = sorted({round(b - a, 6) for a, b in zip(rs, rs[1:])})
        world.gate.write_text(T.gate_text(T.filled(**changes)), encoding="utf-8")
        try:
            report, _ = world.run("held_out")
        except score.Refused as exc:
            print(f"PASS [{label}] refused: {exc}")
            continue
        print(f"RESULT [{label}] ask spacing={spacing} verdict={report['verdict']} "
              f"variant.pacing_s={report['variant']['pacing_s']} onset={report['metrics']['onset']['value']} "
              f"onset.status={report['metrics']['onset']['status']} D={report['service']['D']} "
              f"variant.prompt.sha256={report['variant']['prompt']['sha256'][:8]}...")
