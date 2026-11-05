"""probe_decision_schema: den-core's logged Decision and correction records against schemas.json (canon).

Runs the README's synthetic evening (--touch --game, need incumbent decide), then compares every logged
decision with the canonical Decision record in schemas.json: the `status` and `trigger` enums, the
`candidates` element keys, the `requested_scene` per-channel keys and the presence of `applied_scene`.
It also checks whether den-core ever writes a Correction record (rung 0's other output), and the shape of
the correction it takes as input against the canonical Correction (one per human action, `changes[]`).

    python3 probe_decision_schema.py /path/to/den-core-checkout /path/to/live-v1/schemas.json
"""
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

root = Path(sys.argv[1]).resolve()
schemas = json.loads(Path(sys.argv[2]).read_text("utf-8"))
records = {r["id"]: r for r in schemas["records"]}
fields = {f["name"]: f for f in records["decision"]["fields"]}
status_enum = {s.strip() for s in fields["status"]["type"].split("|")}
trigger_enum = {s.strip() for s in re.sub(r"\(.*", "", fields["trigger"]["meaning"]).split("|")}
cand_keys = set(re.findall(r"\w+", fields["candidates"]["type"]))
req_keys = set(re.findall(r"\{([^}]*)\}", fields["requested_scene"]["type"])[0].replace(" ", "").split(","))
print("RESULT: canon status %s; trigger %s; candidates keys %s; requested_scene keys %s" % (
    sorted(status_enum), sorted(trigger_enum), sorted(cand_keys), sorted(req_keys)))

tmp = Path(tempfile.mkdtemp(prefix="probe-schema-"))
out = tmp / "evening-1"
r = subprocess.run([sys.executable, "-B", str(root / "tools" / "den-core" / "run.py"), "--synthetic", "evening",
                    "--touch", "--game", "--policies", "need", "incumbent", "decide", "--out", str(out)],
                   cwd=str(root), capture_output=True, text=True)
assert r.returncode == 0, r.stderr
log = [json.loads(line) for line in (out / "log.jsonl").read_text("ascii").splitlines()[1:]]
dec = [e["data"] for e in log if e["kind"] == "decision"]
bad = Counter()
for d in dec:
    if d["status"] not in status_enum:
        bad["status=%s" % d["status"]] += 1
    if d["trigger"] not in trigger_enum:
        bad["trigger=%s" % d["trigger"]] += 1
    for c in d["candidates"]:
        if set(c) != cand_keys:
            bad["candidate keys %s" % sorted(c)] += 1
    for ch, s in d["requested_scene"].items():
        if not set(s) <= req_keys:
            bad["requested_scene keys %s" % sorted(s)] += 1
    if "applied_scene" not in d:
        bad["no applied_scene"] += 1
print("RESULT: %d decisions logged" % len(dec))
for what, n in sorted(bad.items()):
    print("FAIL: %d decision(s) with %s (not in schemas.json Decision)" % (n, what))
corr_out = [e for e in log if e["kind"] == "correction" and e["source"] not in ("synthetic",)]
corr_in = [e["data"] for e in log if e["kind"] == "correction"]
print("RESULT: correction records written by den-core itself: %d; correction inputs: %d" % (len(corr_out), len(corr_in)))
canon = [f["name"] for f in records["correction"]["fields"]]
for c in corr_in:
    missing = [n for n in ("changes", "ha_context", "hold", "action") if n not in c]
    print("%s: correction input keys %s; canon Correction fields include %s; missing %s" % (
        "FAIL" if missing else "PASS", sorted(c), [n for n in canon if n in ("changes", "ha_context", "hold", "action")],
        missing))
if not bad and not corr_in:
    print("PASS")
