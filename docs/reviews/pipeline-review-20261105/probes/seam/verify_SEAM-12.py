#!/usr/bin/env python3
"""verify_SEAM-12: do den-observe's Observation lines carry a record version?

    TMPDIR=<scratch dir> python3 -B verify_SEAM-12.py <rung-2-observe checkout> [<rung-3-track checkout>]

The second checkout defaults to a sibling folder named rung-3-track.
Synthetic data only, temp dirs only, no network, python -B in every child.

Steps
 1. replay.py --demo --out <tmp> (den-observe's own synthetic path).
 2. Any version-like key on the Observation lines (version, schema_version,
    schema, record_version, contract_version)?
 3. What a reader at the pinned rung-3 commit does with them
    (den-track records.read_observations: version check and counts).
 4. For comparison: does den-observe's own run.json name a record version for the lines?
Prints PASS/FAIL/RESULT lines. "FAIL" means the lines carry no version.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

TRACK = r'''
import json, sys
sys.dont_write_bytecode = True
track, path = sys.argv[1], sys.argv[2]
sys.path.insert(0, track)
import method, records
frames, counts = records.read_observations(path, method.Method().values["input"])
print(json.dumps({"counts": counts, "OBSERVATION_VERSION": records.OBSERVATION_VERSION}))
'''


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    obs_root = Path(sys.argv[1]).resolve()
    track_root = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else obs_root.parent / "rung-3-track"
    obs, track = obs_root / "tools" / "den-observe", track_root / "tools" / "den-track"
    work = Path(tempfile.mkdtemp(prefix="verify-seam12-"))
    out = work / "observe"
    proc = subprocess.run([sys.executable, "-B", str(obs / "replay.py"), "--demo", "--out", str(out)],
                          capture_output=True, text=True)
    print(f"RESULT demo exit {proc.returncode}; folder {out}")
    recs = [json.loads(l) for l in (out / "observations.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    keys = sorted(set().union(*(set(r) for r in recs)))
    versionish = [k for k in keys if k in ("version", "schema_version", "schema", "record_version", "contract_version")]
    print(f"RESULT step2 keys on {len(recs)} Observation lines: {keys}")
    print(("FAIL" if not versionish else "PASS") + f" step2 version-like keys: {versionish}")
    proc = subprocess.run([sys.executable, "-B", "-c", TRACK, str(track), str(out / "observations.jsonl")],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        t = json.loads(proc.stdout.strip().splitlines()[-1])
        print(f"RESULT step3 den-track reads them: counts {t['counts']} (expects {t['OBSERVATION_VERSION']} when present)")
    else:
        print("RESULT step3 den-track refused: " + proc.stderr.strip().splitlines()[-1])
    run = json.loads((out / "run.json").read_text(encoding="utf-8"))
    print(f"RESULT step4 run.json schema {run.get('schema')!r}; any observation record version in run.json: "
          f"{[k for k in run if 'version' in k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
