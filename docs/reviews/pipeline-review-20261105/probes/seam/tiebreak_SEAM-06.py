#!/usr/bin/env python3
"""tiebreak_SEAM-06: den-core's Decision.trigger values ('time step', 'mode change') sit outside the canon enum,
and decisions carry extra keys. Does any built consumer or any ladder gate read trigger, and does den-verify
(the only built Decision consumer) reject or treat a non-canon trigger differently?

    python3 -B tiebreak_SEAM-06.py <den-core-live checkout root> <rung-10-verify checkout root> [other roots ...]

Synthetic data and temp dirs only; no network or hardware.
 1. canon trigger enum from the den-core-live checkout's docs/architecture/live-v1/schemas.json;
 2. den-core synthetic evening (run.py --synthetic evening --touch --policies need incumbent decide): trigger
    values per policy, with the input kind that caused each decision; extra / absent top-level keys per policy;
 3. every non-test .py line in the given checkouts that reads a 'trigger' key, and ladder.json mentions;
 4. den-verify verify.py run --preview on one synthetic hue-den-4 decision with trigger set to each of
    'person_state change', 'time of day', 'time step', 'mode change': exit code and whether reports match.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

core_root, verify_root = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
others = [Path(a).resolve() for a in sys.argv[3:]]
core = core_root / "tools" / "den-core"
vdir = verify_root / "tools" / "den-verify"
spec = core_root / "docs/architecture/live-v1"
schemas = json.loads((spec / "schemas.json").read_text(encoding="utf-8"))
drec = next(r for r in schemas["records"] if r["id"] == "decision")
meaning = next(f["meaning"] for f in drec["fields"] if f["name"] == "trigger")
canon = [s.strip() for s in re.sub(r"\(.*?\)", "", meaning).split("|")]
print("RESULT canon trigger enum:", canon)
names = set()
for f in drec["fields"]:
    for n in re.split(r"\s*/\s*", f["name"]):
        names.add(n.split(".")[0].strip())

tmp = Path(tempfile.mkdtemp(prefix="tb06-"))
p = subprocess.run([sys.executable, "-B", str(core / "run.py"), "--out", str(tmp / "core"), "--synthetic", "evening",
                    "--touch", "--policies", "need", "incumbent", "decide"], capture_output=True, text=True,
                   cwd=str(tmp))
print("RESULT run.py synthetic evening exit", p.returncode)
log = [json.loads(l) for l in (tmp / "core" / "log.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
counts, keys = {}, {}
last_input = None
for e in log:
    if e.get("source") not in ("need", "incumbent", "decide"):
        last_input = e.get("kind")
    if e.get("kind") != "decision":
        continue
    d = e["data"]
    key = (d["policy"], d["trigger"], last_input)
    counts[key] = counts.get(key, 0) + 1
    keys.setdefault(d["policy"], set()).update(d)
for (pol, trig, cause), n in sorted(counts.items()):
    print("RESULT %-20s trigger=%-22r caused by input kind %-15s x%d%s" % (pol, trig, cause, n,
                                                                           "" if trig in canon else "  NON-CANON"))
for pol, ks in sorted(keys.items()):
    print("RESULT %s extra top-level keys: %s; canon keys absent: %s" % (
        pol, sorted(ks - names), sorted(k for k in ("targets", "predicted", "verify", "applied_scene", "recipients")
                                        if k not in ks)))

readers = []
for root in [core_root, verify_root] + others:
    for py in root.glob("tools/**/*.py"):
        if py.name.startswith("test_") or "__pycache__" in py.parts:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(r"""\[\s*["']trigger["']\s*\]|\.get\(\s*["']trigger["']|trigger\s*==""", line):
                readers.append("%s:%d: %s" % (py.relative_to(root), n, line.strip()[:110]))
print("RESULT non-test lines reading a trigger value (%d):" % len(readers))
for r in readers:
    print("    ", r)
print("RESULT ladder.json mentions of 'trigger':", (spec / "ladder.json").read_text(encoding="utf-8").lower()
      .count("trigger"))

sys.dont_write_bytecode = True
sys.path.insert(0, str(vdir))
import synthetic as syn  # noqa: E402

b0 = syn.HUE[0]
model = syn.hue_model({"desk-1": {b0: 400}})
t = syn.T0
reports = {}
for trig in ("person_state change", "time of day", "time step", "mode change"):
    folder = tmp / ("v-" + trig.replace(" ", "_"))
    folder.mkdir()
    ref = syn.light_state(t, {"desk-1": 150.0}, anchored=False, ident="ls-ref")
    after = syn.light_state(t + 4, {"desk-1": 150.0 + 0.8 * 400}, anchored=False)
    dec = syn.hue_decision("d-1", t + 0.5, {b0: 0.2},
                           {b0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.5}]}}, "ls-ref")
    dec["trigger"] = trig
    (folder / "decisions.jsonl").write_text(json.dumps(dec) + "\n", encoding="utf-8")
    (folder / "ls.jsonl").write_text("".join(json.dumps(r) + "\n" for r in (ref, after)), encoding="utf-8")
    (folder / "m.json").write_text(json.dumps(model), encoding="utf-8")
    q = subprocess.run([sys.executable, "-B", str(vdir / "verify.py"), "run", "--decisions",
                        str(folder / "decisions.jsonl"), "--light-states", str(folder / "ls.jsonl"), "--model",
                        str(folder / "m.json"), "--utc-offset-h", "-7", "--out", str(folder / "out"), "--preview"],
                       capture_output=True, text=True)
    rep = {}
    if q.returncode == 0:
        rep = json.loads((folder / "out" / "report-preview.json").read_text(encoding="utf-8"))
    body = json.dumps({k: v for k, v in rep.items() if k not in ("inputs", "created", "run_id")}, sort_keys=True)
    reports[trig] = (q.returncode, body)
    print("RESULT den-verify trigger=%-22r exit %d; last line: %s" % (
        trig, q.returncode, ((q.stdout + q.stderr).strip().splitlines() or [""])[-1][:120]))
same = len({v for v in reports.values()}) == 1
print("RESULT den-verify reports identical across trigger values:", same)
noncanon = sorted({trig for (_, trig, _) in counts} - set(canon))
print("VERDICT non-canon trigger values written: %s; built readers of a decision's trigger outside den-core's own "
      "decide/run: %s; den-verify indifferent: %s" % (
          noncanon, [r for r in readers if "den-core" not in r and "decide.py" not in r] or "none", same))
print("tmp:", tmp)
