#!/usr/bin/env python3
"""verify_SEAM-06: Decision.trigger values den-core writes vs the canon enum, and whether a built consumer cares.

    python3 -B verify_SEAM-06.py <wt-root containing den-core-live/, rung-10-verify/ and live-records/>

1. Canon trigger enum from schemas.json.
2. den-core's synthetic evening (need incumbent decide): trigger values per policy, extra top-level keys.
3. den-verify (the only built Decision consumer): does any of its code read 'trigger'? Does a decision with
   trigger 'time step' score differently from the same decision with 'person_state change'?
Temp dirs only; no network or hardware."""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

wt = Path(sys.argv[1]).resolve()
core = wt / "den-core-live" / "tools" / "den-core"
vdir = wt / "rung-10-verify" / "tools" / "den-verify"
S = json.loads((wt / "live-records/docs/architecture/live-v1/schemas.json").read_text(encoding="utf-8"))
drec = next(r for r in S["records"] if r["id"] == "decision")
f = next(x for x in drec["fields"] if x["name"] == "trigger")
canon = [s.strip() for s in re.sub(r"\(.*?\)", "", f["meaning"]).split("|")]
print("RESULT canon trigger enum:", canon)
names = set()
for x in drec["fields"]:
    for n in re.split(r"\s*/\s*", x["name"]):
        names.add(n.split(".")[0].strip())
tmp = Path(tempfile.mkdtemp(prefix="v06-"))
subprocess.run([sys.executable, "-B", str(core / "run.py"), "--out", str(tmp / "core"), "--synthetic", "evening",
                "--policies", "need", "incumbent", "decide"], capture_output=True, text=True, cwd=str(tmp))
log = [json.loads(l) for l in open(tmp / "core" / "log.jsonl", encoding="utf-8") if l.strip()]
decs = [e["data"] for e in log if e.get("kind") == "decision"]
by = {}
for d in decs:
    by.setdefault(d["policy"], {}).setdefault(d["trigger"], 0)
    by[d["policy"]][d["trigger"]] += 1
print("RESULT triggers written:", by)
print("RESULT non-canon trigger values:", sorted({d["trigger"] for d in decs} - set(canon)))
print("RESULT top-level keys not named in schemas.json Decision:", sorted({k for d in decs for k in d} - names))
reads = [(p.name, n) for p in vdir.glob("*.py") if not p.name.startswith("test_")
         for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if "trigger" in line]
print("RESULT den-verify non-test lines mentioning 'trigger':", reads)
sys.path.insert(0, str(vdir))
import synthetic as syn  # noqa: E402
B0 = syn.HUE[0]
model = syn.hue_model({"desk-1": {B0: 400}})
t = syn.T0
outs = {}
for trig in ("person_state change", "time step"):
    folder = tmp / trig.replace(" ", "_")
    folder.mkdir()
    ref = syn.light_state(t, {"desk-1": 150.0}, anchored=False, ident="ls-ref")
    after = syn.light_state(t + 4, {"desk-1": 150.0 + 0.8 * 400 * 1.0}, anchored=False)
    dec = syn.hue_decision("d-1", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": [{"kind": "send",
                                                                                   "sent_at": t + 0.5}]}}, "ls-ref")
    dec["trigger"] = trig
    for name, recs in (("decisions.jsonl", [dec]), ("ls.jsonl", [ref, after])):
        (folder / name).write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    (folder / "m.json").write_text(json.dumps(model), encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", str(vdir / "verify.py"), "run", "--decisions", str(folder / "decisions.jsonl"),
                        "--light-states", str(folder / "ls.jsonl"), "--model", str(folder / "m.json"), "--utc-offset-h",
                        "-7", "--out", str(folder / "out"), "--preview"], capture_output=True, text=True)
    rep = json.loads((folder / "out" / "report-preview.json").read_text(encoding="utf-8")) if p.returncode == 0 else {}
    outs[trig] = (p.returncode, json.dumps(rep.get("decisions")), json.dumps(rep.get("points")))
    print("RESULT den-verify with trigger=%r: exit %d, decisions %s" % (trig, p.returncode, outs[trig][1][:160]))
same = outs["person_state change"] == outs["time step"]
print("RESULT den-verify output identical for both triggers:", same)
print("PASS (enum violation reproduced)" if set(d["trigger"] for d in decs) - set(canon) else "FAIL (no violation)")
print("tmp:", tmp)
