#!/usr/bin/env python3
"""verify_SEAM-13: can den-verify (rung 10) read the decisions den-core-live (rung 9) writes?

    python3 -B verify_SEAM-13.py <wt-root containing den-core-live/ and rung-10-verify/>

1. den-core's run.py on its own synthetic evening, --policies need incumbent decide: list the files it writes.
2. den-verify's CLI (--preview) with --decisions <den-core's log.jsonl>, using den-verify's OWN synthetic
   hue_model (so rung 8's adapter is out of the picture) and an empty light-states file.
3. Control: the same decisions unwrapped from the den-event-1 envelopes, one Decision per line.
Temp dirs only; no network or hardware."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

wt = Path(sys.argv[1]).resolve()
core = wt / "den-core-live" / "tools" / "den-core"
vdir = wt / "rung-10-verify" / "tools" / "den-verify"
tmp = Path(tempfile.mkdtemp(prefix="v13-"))
out = tmp / "core"
p = subprocess.run([sys.executable, "-B", str(core / "run.py"), "--out", str(out), "--synthetic", "evening",
                    "--policies", "need", "incumbent", "decide"], capture_output=True, text=True, cwd=str(tmp))
print("RESULT den-core run.py exit %d; files written: %s" % (p.returncode, sorted(x.name for x in out.iterdir())))
log = [json.loads(l) for l in open(out / "log.jsonl", encoding="utf-8") if l.strip()]
kinds = {}
for e in log:
    kinds[e.get("kind")] = kinds.get(e.get("kind"), 0) + 1
dec = [e for e in log if e.get("kind") == "decision"]
print("RESULT log.jsonl line kinds:", kinds)
print("RESULT one decision line's top-level keys:", sorted(dec[0]), "| data has 'version'/'schema_version':",
      "version" in dec[0]["data"] or "schema_version" in dec[0]["data"])
sys.path.insert(0, str(vdir))
import synthetic as syn  # noqa: E402
model = tmp / "hue-model.json"
model.write_text(json.dumps(syn.hue_model({"desk-1": {syn.HUE[0]: 400}})), encoding="utf-8")
empty = tmp / "light_states.jsonl"
empty.write_text("", encoding="utf-8")
unwrapped = tmp / "decisions.jsonl"
unwrapped.write_text("".join(json.dumps(e["data"]) + "\n" for e in dec), encoding="utf-8")
codes = {}
for label, path in (("log.jsonl (den-event-1 envelopes)", out / "log.jsonl"),
                    ("decisions unwrapped, one per line (control)", unwrapped)):
    q = subprocess.run([sys.executable, "-B", str(vdir / "verify.py"), "run", "--decisions", str(path),
                        "--light-states", str(empty), "--model", str(model), "--out",
                        str(tmp / ("v-" + path.stem)), "--preview"], capture_output=True, text=True)
    lines = (q.stdout + q.stderr).strip().splitlines() or [""]
    codes[label] = q.returncode
    tb = "Traceback" in q.stderr
    print("RESULT den-verify on %s: exit %d, traceback=%s: %s" % (label, q.returncode, tb, lines[-1][:200]))
ok = codes["log.jsonl (den-event-1 envelopes)"] == 1 and codes["decisions unwrapped, one per line (control)"] == 0
print("PASS (defect reproduced): den-verify crashes on den-core's only decision output" if ok else
      "FAIL (defect not reproduced)")
print("tmp:", tmp)
