#!/usr/bin/env python3
"""Print need's engagement timeline and the decisions for one harness run folder (SEAM-04 evidence)."""
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
ps = [json.loads(l) for l in open(run / "core-den-core-live/person_state.jsonl")]
t0 = ps[0]["emitted_ts"]
print("track_ids:", sorted({p["track_id"] for p in ps}))
print(" ".join("%d:%s" % (p["emitted_ts"] - t0, p["focused_engagement"]["state"][:4]) for p in ps))
for l in open(run / "core-den-core-live/decision.jsonl"):
    d = json.loads(l)
    print("decision +%.1f s %s %s: %s" % (d["ts"] - t0, d["policy"], d["trigger"], d["why"][:70]))
bs = [json.loads(l) for l in open(run / "bundles-real.jsonl")]
far = [(round(b["interval"][1] - t0, 1), b["track_id"]) for b in bs
       if b["track_id"] and sum(p["x"] for p in b["positions"]) / max(1, len(b["positions"])) > 1.2]
print("bundles read as 'reading' (mean x > 1.2 m):", far)
