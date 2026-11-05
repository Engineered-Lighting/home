#!/usr/bin/env python3
"""Probe for SEAM-04: does merging A's and B's bundles into one room-level need change the decision?

Runs den-bundle's real route on its own two_tracks fixture (A at the counter, B in the armchair, 70 s,
alternating 8 s each), the FAKE INTERPRETER on (a) every bundle and (b) only B's bundles, then den-core-live's
real need/incumbent/decide on each, and compares engagement and decisions."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_seam as S  # noqa: E402

wt = Path(sys.argv[1]).resolve()
out = Path(tempfile.mkdtemp(prefix="collapse-", dir=sys.argv[2])).resolve()
schemas = wt / "live-records/docs/architecture/live-v1/schemas.json"
cli = wt / "rung-5-bundle/tools/den-bundle/den_bundle.py"
fx = out / "fx"
subprocess.run([sys.executable, "-B", str(cli), "fixtures", "--out", str(fx)], check=True, capture_output=True)
bun = out / "bundles.jsonl"
p = subprocess.run([sys.executable, "-B", str(cli), "route", "--observations", str(fx / "two_tracks/observations.jsonl"),
                    "--tracks", str(fx / "two_tracks/tracks.jsonl"), "--from", "2.5", "--to", "70",
                    "--requests", str(out / "requests.jsonl"), "--bundles", str(bun)], capture_output=True, text=True)
print("route:", p.returncode, p.stdout.strip().splitlines()[:3])
bundles = [json.loads(l) for l in open(bun) if l.strip()]
only_b = out / "bundles-B.jsonl"
only_b.write_text("".join(json.dumps(b) + "\n" for b in bundles if b["track_id"] == "B"), encoding="utf-8")
for name, path in (("A+B", bun), ("B only", only_b)):
    sub = out / name.replace(" ", "_").replace("+", "and")
    sub.mkdir()
    facts, bs = S.fake_interpreter(wt, sub, path)
    target = S.core(wt, sub, facts, bs, schemas, "den-core-live")
    ps = [json.loads(l) for l in open(target / "person_state.jsonl")]
    dec = [json.loads(l) for l in open(target / "decision.jsonl")]
    print(f"== {name}: {len(bs)} bundles; engagement states:",
          [p["focused_engagement"]["state"] for p in ps])
    for d in dec:
        print("   decision", d["policy"], d["trigger"], d["why"][:120],
              {k.split('.')[-1]: (v or {}).get("brightness") if isinstance(v, dict) else v
               for k, v in d["requested_scene"].items()})
print(out)
