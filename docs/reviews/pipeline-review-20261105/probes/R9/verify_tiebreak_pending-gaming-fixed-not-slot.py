#!/usr/bin/env python3
"""Tiebreak probe for pending-gaming-fixed-not-slot: how game time reaches rung 9's verdict on a real-file log.

    python3 -B verify_tiebreak_pending-gaming-fixed-not-slot.py <den-core-live checkout root>

Real exports carry no gaming_sample (only synthetic.py makes them); a game shows only as live_status, a reference
no policy sees (events.REFERENCE_KINDS). This builds two non-synthetic logs (test_rung9's scored_log construct:
decide's slots filled, a non-fake copy of the fake light model) from the synthetic evening without Steam samples:
  NOLIVE:   no live_status;
  LIVEGAME: live_status {automatic: true, gaming: true} from T0+640 s to T0+900 s (as den_lighting_status would
            give for a game across reading block b4), source den_lighting_status.
It scores both with a filled gate and prints refusals, the episodes, the candidate's rates, and the verdict; and it
lists decide's decisions inside the live game. If LIVEGAME scores exactly like NOLIVE, the episode overlapping the
live game is admitted with decide acting in it, which neither gaming option allows (non-gaming-stratified: game
time leaves the comparison; complete-policy: the candidate reproduces gaming). Temp copy, synthetic data, no host.
"""
import os
import shutil
import subprocess
import sys
import tempfile

COPY = ["tools/den-core", "ha-config/custom_components/den_lighting", "stack/services/perception-lab",
        "tools/la-latency"]
DRIVER = r'''
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, ".")
import decide, events, light_model, runner, score, synthetic
T0 = synthetic.T0
tmp = Path(tempfile.mkdtemp(prefix="r9-tb-gaming-"))
gate = json.loads(score.GATE.read_text("utf-8"))
for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
    entry.update(value=entry["proposed"], status="set")
gate["parameters"]["compensation"]["value"] = "none-v0"
gp = tmp / "gate.json"; gp.write_text(json.dumps(gate), "ascii")
model = light_model.fake(); model.update(version="probe-lm-1", fake=False)
slots = {n: {**e, "value": e["proposed"], "status": "set"} for n, e in decide.DEFAULT_CONFIG["slots"].items()}
out = {}
for name, live in (("NOLIVE", False), ("LIVEGAME", True)):
    items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True)
    inputs = runner.tagged(items, "synthetic")
    if live:
        inputs += [(T0 + 640, "live_status", {"automatic": True, "gaming": True}, "den_lighting_status"),
                   (T0 + 900, "live_status", {"automatic": True, "gaming": False}, "den_lighting_status")]
    header, log = runner.run(inputs, runner.load_policies(["need", "incumbent", "decide"],
                             {"decide": {"slots": slots, "light_model": model}}), synthetic=False,
                             sources=[{"name": "probe:%s" % name}])
    path = tmp / (name + ".jsonl"); events.write(path, header, log)
    record, reasons = score.score(path, gp)
    found = score.episodes(log)
    inside = [(round(e["t"] - T0, 1), score.rule(e["data"])) for e in log
              if e["kind"] == "decision" and e["source"] == "decide" and T0 + 640 <= e["t"] <= T0 + 900]
    games = [e["t"] - T0 for e in log if e["kind"] == "mode" and e["source"] == "incumbent" and e["data"]["gaming"]]
    arms = record["surrogates"]["arms"]["candidate"]
    print("%s: refusals=%s live_status events=%d incumbent mode gaming events=%d" % (
        name, reasons, sum(e["kind"] == "live_status" for e in log), len(games)))
    print("%s: episodes (s after T0)=%s" % (name, [(round(a - T0, 1), round(b - T0, 1)) for _, a, b in found]))
    print("%s: decide decisions inside the live game window 640-900 s: %s" % (name, inside))
    print("%s: candidate anticipation=%s stability=%s verdict=%s" % (
        name, arms["anticipation"]["rate"], arms["stability"]["rate"], record["verdict"]))
    out[name] = (arms["anticipation"], arms["stability"], record["verdict"], [c["ok"] for c in record["checks"].values()])
same = out["NOLIVE"] == out["LIVEGAME"]
print("RESULT live game changes nothing in the score: %s" % same)
print("RESULT %s" % ("CONFIRMED: an episode overlapping a live game is admitted, scored with decide acting in it"
                     if same else "NOT REPRODUCED"))
'''


def main():
    root = os.path.abspath(sys.argv[1])
    work = tempfile.mkdtemp(prefix="r9-tb-gaming-copy-")
    try:
        for rel in COPY:
            src = os.path.join(root, rel)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(work, rel), ignore=shutil.ignore_patterns("__pycache__"))
        p = subprocess.run([sys.executable, "-B", "-c", DRIVER], cwd=os.path.join(work, "tools", "den-core"),
                           capture_output=True, text=True, timeout=900)
        print(p.stdout.strip())
        if p.returncode:
            print("FAIL driver error:\n" + p.stderr.strip()[-3000:])
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
