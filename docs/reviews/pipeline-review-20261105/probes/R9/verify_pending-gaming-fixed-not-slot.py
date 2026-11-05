#!/usr/bin/env python3
"""Verify finding pending-gaming-fixed-not-slot (rung 9, tools/den-core/score.py + decide.py).

    python3 -B verify_pending-gaming-fixed-not-slot.py <den-core-live checkout root>

What it checks, in a temp copy of the checkout's tools (the checkout is never touched; synthetic data only;
nothing binds or reads a host):
 1. decide's logged config carries 'gaming' under config['decisions'] (not config['slots']), so score.refusals()
    cannot see it; prints open_slots() of the config and of the gate (no 'gaming').
 2. Builds two non-synthetic test logs (the same test-only construct as test_rung9.ScoreTest.scored_log: decide's
    slots filled, a non-fake copy of the fake light model) from the synthetic evening:
      NOGAME: the evening, no game;
      GAME:   the same evening with a game (Steam 'playing' samples) running across reading block b4.
    Scores both with a filled gate (as ScoreTest.gate() does) and prints refusal reasons, per-arm anticipation /
    stability, the episodes, and the verdict. If GAME gives reasons == [] and a pass/fail verdict while an
    episode overlaps game time, score.py admits a pass/fail on a case that depends on {{dec:gaming}}.
"""
import json
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

def filled(config_slots):
    return {n: {**e, "value": e["proposed"], "status": "set"} for n, e in config_slots.items()}

def real_model():
    m = light_model.fake(); m.update(version="test-lm-1", fake=False); return m

tmp = Path(tempfile.mkdtemp(prefix="verify-gaming-"))
gate = json.loads(score.GATE.read_text("utf-8"))
for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
    entry.update(value=entry["proposed"], status="set")
gate["parameters"]["compensation"]["value"] = "none-v0"
gate_path = tmp / "gate.json"; gate_path.write_text(json.dumps(gate), "ascii")

cfg = decide.make_policy().config
print("CONFIG decisions.gaming = %s" % json.dumps(cfg["decisions"]["gaming"]))
print("CONFIG open_slots(config.slots) = %s" % score.open_slots(cfg["slots"]))
print("GATE   open_slots(filled gate) = %s ; 'gaming' in gate text: %s" % (score.open_slots(gate), "gaming" in json.dumps(gate)))

def build(name, game_window):
    items, truth, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True)
    if game_window:
        a, b = game_window
        samples = [(dt, "playing") for dt in range(a, b, 10)] + [(dt, "idle") for dt in range(b, b + 61, 10)]
        items = sorted(items + [(T0 + dt, "gaming_sample", {"status": s, "sampled_at": T0 + dt - 0.5}) for dt, s in samples],
                       key=lambda x: x[0])
    configs = {"decide": {"slots": filled(decide.DEFAULT_CONFIG["slots"]), "light_model": real_model()}}
    header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(("need", "incumbent", "decide"), configs),
                             synthetic=False, sources=[{"name": "synthetic:evening", "params": {}}])
    path = tmp / (name + ".jsonl"); events.write(path, header, log)
    games = []
    for e in log:
        if e["kind"] == "mode" and e["source"] == "incumbent":
            if e["data"]["gaming"] and (not games or games[-1][1] is not None):
                games.append([e["t"] - T0, None])
            elif not e["data"]["gaming"] and games and games[-1][1] is None:
                games[-1][1] = e["t"] - T0
    record, reasons = score.score(path, gate_path)
    found = score.episodes(log)
    print("%s: incumbent game spans (s after T0) = %s" % (name, games))
    print("%s: episodes (track, onset, end, s after T0) = %s" % (name, [(t, round(a - T0, 1), round(b - T0, 1)) for t, a, b in found]))
    print("%s: refusal reasons = %s" % (name, reasons))
    for arm in ("candidate", "incumbent"):
        print("%s: %s anticipation=%s stability=%s" % (name, arm, record["surrogates"]["arms"][arm]["anticipation"],
                                                     record["surrogates"]["arms"][arm]["stability"]))
    print("%s: checks.anticipation=%s" % (name, record["checks"]["anticipation"]))
    print("%s: verdict = %s" % (name, record["verdict"]))
    return record, reasons, found, games

r0, _, _, _ = build("NOGAME", None)
r1, reasons, found, games = build("GAME", (640, 900))
overlap = [(t, a - T0, b - T0) for t, a, b in found for g0, g1 in games if a - T0 < (g1 if g1 is not None else 1e9) and b - T0 > g0]
print("RESULT game-overlapping episodes scored: %s" % overlap)
if not reasons and r1["verdict"] in ("pass", "fail") and overlap:
    print("RESULT CONFIRMED: no refusal and verdict %s on a log whose episode overlaps game time (gaming pending)" % r1["verdict"])
else:
    print("RESULT NOT REPRODUCED")
'''


def main():
    root = os.path.abspath(sys.argv[1])
    work = tempfile.mkdtemp(prefix="verify-r9-gaming-")
    try:
        for rel in COPY:
            src = os.path.join(root, rel)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(work, rel), ignore=shutil.ignore_patterns("__pycache__"))
        core = os.path.join(work, "tools", "den-core")
        p = subprocess.run([sys.executable, "-B", "-c", DRIVER], cwd=core, capture_output=True, text=True, timeout=900)
        print(p.stdout.strip())
        if p.returncode:
            print("FAIL driver error:\n" + p.stderr.strip()[-3000:])
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
